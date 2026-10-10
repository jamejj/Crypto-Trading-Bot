"""Atomic fake-only intents/outbox and ledger-backed order evidence."""

import json
from dataclasses import replace
from datetime import timedelta

from trading_bot.domain.records import OrderIntent, RiskApproval
from trading_bot.domain.serialization import utc
from trading_bot.execution.intents import prepare_intent
from trading_bot.execution.protection import terminal_partial
from trading_bot.execution.reducer import TERMINAL, OrderState, apply
from trading_bot.execution.submission import SubmitWindow, UnresolvedSubmit

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

    def _pending_submits(self, conn):
        rows = conn.execute(
            "SELECT o.intent_id,o.state,p.projection,w.evidence FROM execution_outbox o "
            "JOIN execution_orders p USING(account_id,intent_id) "
            "LEFT JOIN execution_submit_windows w USING(account_id,intent_id) "
            "WHERE o.account_id=%s ORDER BY o.intent_id",
            (self.account_id,),
        ).fetchall()
        pending = []
        for intent_id, status, projection, window in rows:
            order = OrderState.from_json(json.dumps(projection))
            actual = order.filled.amount if order.filled else 0
            missing = any(o.cumulative_quantity.amount > actual for o in order.observations)
            attempted = status in {"DISPATCHING", "SUBMISSION_UNKNOWN", "RESOLVED"}
            if attempted and (status != "RESOLVED" or order.status not in TERMINAL or missing):
                pending.append(
                    (
                        intent_id,
                        status,
                        missing,
                        SubmitWindow.from_json(json.dumps(window)) if window else None,
                    )
                )
        return pending

    def _unresolved(self, conn, exclude=None, now=None):
        if conn.execute(
            "SELECT 1 FROM execution_incidents WHERE account_id=%s LIMIT 1", (self.account_id,)
        ).fetchone():
            raise ValueError("unresolved terminal partial FOK incident blocks account entries")
        for intent_id, status, missing, window in self._pending_submits(conn):
            if intent_id != exclude and (
                missing
                or status != "RESOLVED"
                or window is None
                or (now is not None and utc(now) >= window.deadline)
            ):
                return True
        if conn.execute("SELECT to_regclass('instrument_lifecycle')").fetchone()[0] is not None:
            from trading_bot.execution.protection import ProtectionClock, assess_protection
            from trading_bot.operations.clock import SimulationClock
            from trading_bot.storage.lifecycle_repository import LifecycleRepository, LifecycleState

            book = self.ledger._replay(conn)
            lifecycle = LifecycleRepository(self)
            rows = conn.execute(
                "SELECT projection FROM instrument_lifecycle WHERE account_id=%s",
                (self.account_id,),
            ).fetchall()
            for row in rows:
                instrument = LifecycleState.from_json(json.dumps(row[0])).context.instrument
                state = lifecycle._load(conn, instrument)
                protection = assess_protection(
                    book.snapshot, state.context, ProtectionClock(SimulationClock(utc(now)))
                )
                if (
                    protection.uncovered.amount > 0
                    or (
                        state.exit_request
                        and (
                            protection.target.amount > 0
                            or state.sell_status in {"PENDING", "UNKNOWN"}
                        )
                    )
                    or state.replacing
                    or state.context.stop_status == "UNKNOWN"
                ):
                    return True
        return False

    def _record_window(self, conn, intent_id, received_at):
        row = conn.execute(
            "SELECT o.state,w.evidence FROM execution_outbox o "
            "LEFT JOIN execution_submit_windows w USING(account_id,intent_id) "
            "WHERE o.account_id=%s AND o.intent_id=%s",
            (self.account_id, intent_id),
        ).fetchone()
        if row[1] is not None:
            return
        if row[0] != "PREPARED":
            raise ValueError("attempt lacks original deadline; manual review required")
        window = SubmitWindow(
            self.account_id, intent_id, utc(received_at), utc(received_at) + timedelta(seconds=5)
        )
        conn.execute(
            "INSERT INTO execution_submit_windows VALUES (%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
            (self.account_id, intent_id, window.to_json()),
        )

    def submit_window(self, intent_id):
        with transaction(self.connect) as conn:
            row = conn.execute(
                "SELECT evidence FROM execution_submit_windows "
                "WHERE account_id=%s AND intent_id=%s",
                (self.account_id, intent_id),
            ).fetchone()
            return SubmitWindow.from_json(json.dumps(row[0])) if row else None

    def assess_unresolved(self, clock):
        """Explicit offline assessment, not an automatic retry/recovery worker."""
        with transaction(self.connect) as conn:
            self._book(conn)
            result = []
            for intent_id, _, _, window in self._pending_submits(conn):
                if window is None:
                    raise ValueError(
                        "unresolved pre-migration attempt lacks deadline; manual review required"
                    )
                overdue = clock.overdue(window.deadline)
                result.append(UnresolvedSubmit(window, overdue))
                if overdue:
                    conn.execute(
                        "UPDATE execution_outbox SET state='SUBMISSION_UNKNOWN' "
                        "WHERE account_id=%s AND intent_id=%s",
                        (self.account_id, intent_id),
                    )
            return tuple(result)

    def prepare_intent(self, approval, reservation, payload, *, now):
        intent = prepare_intent(approval, reservation, payload, now=now)
        if intent.account_id != self.account_id:
            raise ValueError("account scope conflict")
        with transaction(self.connect) as conn:
            book = self._book(conn)
            if self._unresolved(conn, now=now):
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

    def _entry_right(self, book, intent):
        original = next(
            (
                args[0]
                for method, args in book.operations
                if method == "_reserve" and args[0].reservation_id == intent.reservation_id
            ),
            None,
        )
        current = book.reservations.get(intent.reservation_id)
        if (
            original is None
            or current != original
            or current.status != "PENDING"
            or current.account_id != intent.account_id
            or current.intent_id != intent.intent_id
            or current.instrument != intent.instrument
            or current.side != "BUY"
            or current.quantity != intent.quantity
            or not current.order_id
            or current.cash.amount <= 0
        ):
            raise PermissionError("active exact entry reservation required")
        # Include the whole account's pending cash/base/fee rights and liabilities.
        # The approval never manufactures replacement rights after release/fill.
        currencies = {
            current.cash.currency,
            current.quantity.base,
            *(m.currency for m in current.fee_buffers),
        }
        if any(book._available(currency) < 0 for currency in currencies):
            raise PermissionError("entry commitments exceed currently owned assets")
        return current

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
            self._entry_right(book, intent)
            if self._unresolved(conn, intent_id, now):
                return False
            self._record_window(conn, intent_id, now)
            row = conn.execute(
                "UPDATE execution_outbox SET state='DISPATCHING' WHERE "
                "account_id=%s AND intent_id=%s AND state='PREPARED' RETURNING 1",
                (self.account_id, intent_id),
            ).fetchone()
            return row is not None

    def _send_time(self, conn, intent, approval, clock, right):
        row = conn.execute(
            "SELECT evidence FROM execution_submit_windows WHERE account_id=%s AND intent_id=%s",
            (self.account_id, intent.intent_id),
        ).fetchone()
        # Read only AFTER potential DB/ownership/fence waits, never reuse call-time UTC.
        now = utc(clock())
        if row is None or now >= min(
            SubmitWindow.from_json(json.dumps(row[0])).deadline,
            approval.expires_at,
            right.expires_at,
        ):
            raise PermissionError("durable window/approval/reservation deadline expired or absent")
        return now

    def _transmit(self, intent, clock, callback):
        """One-shot fake admission, durable UNKNOWN before the external effect."""
        with transaction(self.connect) as conn:
            book = self._book(conn)
            row = conn.execute(
                "SELECT intent,approval,state FROM execution_intents "
                "JOIN execution_outbox USING(account_id,intent_id) "
                "WHERE account_id=%s AND intent_id=%s",
                (self.account_id, intent.intent_id),
            ).fetchone()
            if row is None or row[2] != "DISPATCHING":
                raise PermissionError("durable one-shot claim required")
            durable = OrderIntent.from_json(json.dumps(row[0]))
            approval = RiskApproval.from_json(json.dumps(row[1]))
            self._entry_right(book, durable)
            if replace(intent, status=durable.status) != durable or intent.side != "BUY":
                raise ValueError("transmission differs from immutable entry intent")
            if conn.execute("SELECT to_regclass('instrument_lifecycle')").fetchone()[0] is None:
                raise PermissionError("configured protection required before transmission")
            configured = conn.execute(
                "SELECT 1 FROM instrument_lifecycle WHERE account_id=%s AND instrument=%s",
                (self.account_id, intent.instrument.to_json()),
            ).fetchone()
            if configured is None:
                raise PermissionError("configured protection required before transmission")
            # Validate the durable context against its immutable audit as well.
            from trading_bot.storage.lifecycle_repository import LifecycleRepository

            LifecycleRepository(self)._load(conn, intent.instrument)
            self._send_time(conn, durable, approval, clock, self._entry_right(book, durable))
            conn.execute(
                "UPDATE execution_outbox SET state='SUBMISSION_UNKNOWN' "
                "WHERE account_id=%s AND intent_id=%s",
                (self.account_id, intent.intent_id),
            )
        # Recheck under the account lock: intervening fill/order evidence wins.
        with transaction(self.connect) as conn:
            book = self._book(conn)
            self._entry_right(book, intent)
            row = conn.execute(
                "SELECT state FROM execution_outbox WHERE account_id=%s AND intent_id=%s",
                (self.account_id, intent.intent_id),
            ).fetchone()
            if row != ("SUBMISSION_UNKNOWN",):
                raise PermissionError("order evidence superseded transmission")
            now = self._send_time(conn, intent, approval, clock, self._entry_right(book, intent))
            if self._unresolved(conn, intent.intent_id, now):
                raise PermissionError("unresolved/uncovered exposure blocks transmission")
            self._send_time(conn, intent, approval, clock, self._entry_right(book, intent))
            return callback(intent)

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

    def apply_event(self, intent_id, event, *, protection_clock=None):
        with transaction(self.connect) as conn:
            book = self._book(conn)
            from trading_bot.execution.protection import ProtectionClock
            from trading_bot.operations.clock import RealClock

            clock = protection_clock or ProtectionClock(RealClock())
            state = self._apply_event(conn, book, intent_id, event, clock.utc_now())
            # All public entry evidence paths update configured protection atomically.
            if conn.execute("SELECT to_regclass('instrument_lifecycle')").fetchone()[0] is not None:
                configured = conn.execute(
                    "SELECT 1 FROM instrument_lifecycle WHERE account_id=%s AND instrument=%s",
                    (self.account_id, event.instrument.to_json()),
                ).fetchone()
                if configured:
                    from trading_bot.storage.lifecycle_repository import LifecycleRepository

                    lifecycle = LifecycleRepository(self)
                    current = lifecycle._load(conn, event.instrument)
                    current = lifecycle._assess(conn, book, current, clock)
                    current = lifecycle._manage(conn, book, current, clock)
                    lifecycle._save(conn, current, event)
            return state

    def _apply_event(self, conn, book, intent_id, event, received_at):
        version, count = book.version, len(book.entries)
        row = conn.execute(
            "SELECT projection FROM execution_orders WHERE "
            "account_id=%s AND intent_id=%s FOR UPDATE",
            (self.account_id, intent_id),
        ).fetchone()
        if row is None:
            raise ValueError("unknown intent")
        state = apply(OrderState.from_json(json.dumps(row[0])), event, book)
        self._record_window(conn, intent_id, received_at)
        # Retain positive terminal partial evidence even if late trades later
        # complete the target. Coverage/economic reconciliation is not clearance
        # of a breached FOK semantic contract.
        if terminal_partial(state):
            conn.execute(
                "INSERT INTO execution_incidents "
                "VALUES (%s,%s,'TERMINAL_PARTIAL_FOK',%s,%s::jsonb) "
                "ON CONFLICT DO NOTHING",
                (self.account_id, intent_id, utc(received_at), state.to_json()),
            )
        self._persist(conn, book, version, count)
        conn.execute(
            "INSERT INTO execution_observations(account_id,intent_id,event) "
            "VALUES (%s,%s,%s::jsonb)",
            (self.account_id, intent_id, event.to_json()),
        )
        conn.execute(
            "UPDATE execution_orders SET projection=%s::jsonb WHERE account_id=%s AND intent_id=%s",
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
