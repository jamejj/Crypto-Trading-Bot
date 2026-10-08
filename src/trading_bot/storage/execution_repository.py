"""Atomic fake-only intents/outbox and ledger-backed order evidence."""

import json
from dataclasses import replace

from trading_bot.domain.records import OrderIntent, RiskApproval
from trading_bot.domain.serialization import utc
from trading_bot.execution.intents import prepare_intent
from trading_bot.execution.reducer import OrderState, apply

from .ledger_repository import LedgerRepository
from .transactions import encode_arguments, transaction


class ExecutionRepository:
    def __init__(self, connection_factory, account_id, quote):
        self.connect = connection_factory
        self.account_id = account_id
        self.ledger = LedgerRepository(connection_factory, account_id, quote)

    def _book(self, conn):
        row = self.ledger._lock(conn)
        book = self.ledger._replay(conn)
        if row[1] != book.version or row[2] != json.loads(book.snapshot.to_json()):
            raise ValueError("projection/audit disagreement")
        return book

    def _persist(self, conn, book, version, entry_count):
        if book.version == version:
            return
        method, arguments = book.operations[-1]
        conn.execute(
            "INSERT INTO ledger_operations VALUES (%s,%s,%s,%s::jsonb)",
            (self.account_id, book.version, method, encode_arguments(arguments)),
        )
        for line, posting in enumerate(book.entries[entry_count:]):
            conn.execute(
                "INSERT INTO ledger_postings VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    self.account_id,
                    book.version,
                    line,
                    posting.account,
                    posting.value.currency,
                    posting.value.amount,
                ),
            )
        self.ledger._project(conn, book)

    def _unresolved(self, conn, exclude=None):
        return (
            conn.execute(
                "SELECT 1 FROM execution_outbox WHERE account_id=%s "
                "AND state IN ('DISPATCHING','SUBMISSION_UNKNOWN') AND intent_id<>%s LIMIT 1",
                (self.account_id, exclude or ""),
            ).fetchone()
            is not None
        )

    def prepare_intent(self, approval, reservation, payload, *, now):
        intent = prepare_intent(approval, reservation, payload, now=now)
        if intent.account_id != self.account_id:
            raise ValueError("account scope conflict")
        with transaction(self.connect) as conn:
            book = self._book(conn)
            if self._unresolved(conn):
                raise ValueError("unresolved create blocks account entries")
            version, count = book.version, len(book.entries)
            book.reserve(reservation, reservation.ledger_version)
            conn.execute(
                "INSERT INTO execution_intents VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
                (
                    self.account_id,
                    intent.intent_id,
                    reservation.order_id,
                    approval.approval_id,
                    intent.to_json(),
                    approval.to_json(),
                    payload,
                    intent.payload_hash,
                ),
            )
            conn.execute(
                "INSERT INTO execution_outbox VALUES (%s,%s,'PREPARED')",
                (self.account_id, intent.intent_id),
            )
            state = OrderState(
                intent.intent_id,
                self.account_id,
                intent.instrument,
                reservation.order_id,
                intent.side,
                intent.quantity,
            )
            conn.execute(
                "INSERT INTO execution_orders VALUES (%s,%s,%s::jsonb)",
                (self.account_id, intent.intent_id, state.to_json()),
            )
            self._persist(conn, book, version, count)
        return intent

    def get_intent(self, intent_id):
        with transaction(self.connect) as conn:
            row = conn.execute(
                "SELECT intent, state FROM execution_intents JOIN execution_outbox "
                "USING (account_id,intent_id) WHERE account_id=%s AND intent_id=%s",
                (self.account_id, intent_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown intent")
            return replace(OrderIntent.from_json(json.dumps(row[0])), status=row[1])

    def get_state(self, intent_id):
        with transaction(self.connect) as conn:
            row = conn.execute(
                "SELECT projection FROM execution_orders WHERE account_id=%s AND intent_id=%s",
                (self.account_id, intent_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown intent")
            return OrderState.from_json(json.dumps(row[0]))

    def claim(self, intent_id, *, now):
        with transaction(self.connect) as conn:
            self._book(conn)
            row = conn.execute(
                "SELECT approval FROM execution_intents JOIN execution_outbox "
                "USING(account_id,intent_id) WHERE account_id=%s AND intent_id=%s "
                "AND state='PREPARED'",
                (self.account_id, intent_id),
            ).fetchone()
            if row is None:
                return False
            approval = RiskApproval.from_json(json.dumps(row[0]))
            book = self.ledger._replay(conn)
            intent_row = conn.execute(
                "SELECT intent FROM execution_intents WHERE account_id=%s AND intent_id=%s",
                (self.account_id, intent_id),
            ).fetchone()
            intent = OrderIntent.from_json(json.dumps(intent_row[0]))
            item = book.reservations[intent.reservation_id]
            if min(approval.expires_at, item.expires_at) <= utc(now):
                conn.execute(
                    "UPDATE execution_outbox SET state='ABORTED_BEFORE_SEND' WHERE "
                    "account_id=%s AND intent_id=%s",
                    (self.account_id, intent_id),
                )
                return False
            if self._unresolved(conn, intent_id):
                return False
            row = conn.execute(
                "UPDATE execution_outbox SET state='DISPATCHING' WHERE "
                "account_id=%s AND intent_id=%s AND state='PREPARED' RETURNING 1",
                (self.account_id, intent_id),
            ).fetchone()
            return row is not None

    def recover_ambiguous(self):
        with transaction(self.connect) as conn:
            self._book(conn)
            conn.execute(
                "UPDATE execution_outbox SET state='SUBMISSION_UNKNOWN' WHERE "
                "account_id=%s AND state='DISPATCHING'",
                (self.account_id,),
            )

    def mark_unknown(self, intent_id):
        with transaction(self.connect) as conn:
            self._book(conn)
            conn.execute(
                "UPDATE execution_outbox SET state='SUBMISSION_UNKNOWN' WHERE "
                "account_id=%s AND intent_id=%s AND state='DISPATCHING'",
                (self.account_id, intent_id),
            )

    def apply_event(self, intent_id, event):
        with transaction(self.connect) as conn:
            book = self._book(conn)
            version, count = book.version, len(book.entries)
            row = conn.execute(
                "SELECT projection FROM execution_orders WHERE "
                "account_id=%s AND intent_id=%s FOR UPDATE",
                (self.account_id, intent_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown intent")
            state = apply(OrderState.from_json(json.dumps(row[0])), event, book)
            self._persist(conn, book, version, count)
            conn.execute(
                "INSERT INTO execution_observations(account_id,intent_id,event) "
                "VALUES (%s,%s,%s::jsonb)",
                (self.account_id, intent_id, event.to_json()),
            )
            conn.execute(
                "UPDATE execution_orders SET projection=%s::jsonb WHERE "
                "account_id=%s AND intent_id=%s",
                (state.to_json(), self.account_id, intent_id),
            )
            # A valid scoped trade/order observation proves an existing order even
            # when it reaches us before the local dispatcher response.
            conn.execute(
                "UPDATE execution_outbox SET state='RESOLVED' WHERE "
                "account_id=%s AND intent_id=%s AND "
                "state IN ('PREPARED','DISPATCHING','SUBMISSION_UNKNOWN')",
                (self.account_id, intent_id),
            )
            return state
