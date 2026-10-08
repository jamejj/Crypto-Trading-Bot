"""Offline durable protection/exit coordinator, serialized by the P02 account lock.

This is a local transaction boundary, not P03.5 writer ownership or a live adapter.
Every fake sell right is a P02 SELL reservation; linked exit shares its stop right.
"""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from trading_bot.accounting.arithmetic import ZERO, sub, total
from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import ExitRequest, Fill, OrderObservation, Reservation
from trading_bot.domain.serialization import Record
from trading_bot.execution.exits import ExitState, SyntheticExitVerification, request_exit
from trading_bot.execution.protection import (
    LifecycleCommand,
    ProtectionContext,
    assess_protection,
    propose_stop,
)
from trading_bot.storage.transactions import transaction


@dataclass(frozen=True)
class LifecycleState(Record):
    context: ProtectionContext
    policy: str = "DISABLED"
    verification: SyntheticExitVerification | None = None
    exit_request: ExitRequest | None = None
    sell_id: str = ""
    sell_status: str = "NONE"
    stop_observation: OrderObservation | None = None
    reconciled_version: int | None = None
    serial_opened_at: datetime | None = None
    serial_deadline: datetime | None = None


class LifecycleRepository:
    def __init__(self, execution_repository):
        self.execution = execution_repository
        self.connect = execution_repository.connect
        self.account = execution_repository.account_id

    def _key(self, instrument):
        return instrument.to_json()

    def _load(self, conn, instrument):
        row = conn.execute(
            "SELECT projection FROM instrument_lifecycle WHERE account_id=%s "
            "AND instrument=%s FOR UPDATE",
            (self.account, self._key(instrument)),
        ).fetchone()
        if row is None:
            raise ValueError("unconfigured lifecycle")
        audit = conn.execute(
            "SELECT state FROM lifecycle_audit WHERE account_id=%s AND instrument=%s "
            "ORDER BY sequence DESC LIMIT 1",
            (self.account, self._key(instrument)),
        ).fetchone()
        if audit is None or audit[0] != row[0]:
            raise ValueError("lifecycle projection/audit disagreement")
        return LifecycleState.from_json(json.dumps(row[0]))

    def _save(self, conn, state, evidence=None):
        key = self._key(state.context.instrument)
        conn.execute(
            "UPDATE instrument_lifecycle SET projection=%s::jsonb WHERE account_id=%s "
            "AND instrument=%s",
            (state.to_json(), self.account, key),
        )
        conn.execute(
            "INSERT INTO lifecycle_audit(account_id,instrument,state,evidence) "
            "VALUES (%s,%s,%s::jsonb,%s::jsonb)",
            (self.account, key, state.to_json(), evidence.to_json() if evidence else None),
        )

    def configure(self, context):
        # A restart reads prior state; it never reinitializes timers or orders.
        if context.stop_status != "NONE" or context.protection is not None:
            raise ValueError("configure accepts only empty lifecycle")
        with transaction(self.connect) as conn:
            self.execution._book(conn)
            state = LifecycleState(context)
            conn.execute(
                "INSERT INTO instrument_lifecycle VALUES (%s,%s,%s::jsonb)",
                (self.account, self._key(context.instrument), state.to_json()),
            )
            self._save(conn, state)

    def get(self, instrument):
        with transaction(self.connect) as conn:
            self.execution._book(conn)
            return self._load(conn, instrument)

    def commands(self, instrument):
        with transaction(self.connect) as conn:
            rows = conn.execute(
                "SELECT proposal FROM lifecycle_commands WHERE account_id=%s "
                "AND instrument=%s ORDER BY command_id",
                (self.account, self._key(instrument)),
            ).fetchall()
            return [LifecycleCommand.from_json(json.dumps(row[0])) for row in rows]

    def _operation(self, conn, book, method, *args):
        version, count = book.version, len(book.entries)
        getattr(book, method)(*args)
        self.execution._persist(conn, book, version, count)

    def _reserve(self, conn, book, proposal):
        item = Reservation(
            "lifecycle:" + proposal.command_id,
            proposal.intent_id,
            Money(ZERO, book.quote),
            Money(ZERO, book.quote),
            book.version,
            datetime.max.replace(tzinfo=UTC),
            "PENDING",
            self.account,
            proposal.command_id,
            proposal.instrument,
            "SELL",
            proposal.quantity,
        )
        # P02 checks all account base commitments across markets, not just this instrument.
        self._operation(conn, book, "reserve", item, book.version)

    def _enqueue(self, conn, book, proposal):
        if proposal.kind in {"CREATE_STOP", "MARKET_SELL"}:
            self._reserve(conn, book, proposal)
        conn.execute(
            "INSERT INTO lifecycle_commands VALUES (%s,%s,%s,%s::jsonb)",
            (self.account, proposal.command_id, self._key(proposal.instrument), proposal.to_json()),
        )
        conn.execute(
            "INSERT INTO lifecycle_command_status VALUES (%s,%s,'PENDING')",
            (self.account, proposal.command_id),
        )

    def _assess(self, conn, book, state, clock, *, create=True):
        protection = assess_protection(book.snapshot, state.context, clock)
        state = replace(state, context=replace(state.context, protection=protection))
        proposals = (
            propose_stop(state.context, self.account) if create and not state.exit_request else []
        )
        for proposal in proposals:
            if proposal.quantity.amount > book._available(proposal.instrument.base):
                return replace(
                    state,
                    context=replace(
                        state.context, protection=replace(protection, status="CONFLICT")
                    ),
                )
            self._enqueue(conn, book, proposal)
            state = replace(
                state,
                context=replace(
                    state.context,
                    stop_id=proposal.command_id,
                    stop_quantity=proposal.quantity,
                    stop_status="PENDING",
                    confirmed_valid=False,
                ),
            )
        return state

    def assess(self, instrument, clock):
        with transaction(self.connect) as conn:
            book = self.execution._book(conn)
            state = self._assess(conn, book, self._load(conn, instrument), clock)
            self._save(conn, state)
            return state

    def apply_entry_event(self, intent_id, event, clock):
        with transaction(self.connect) as conn:
            book = self.execution._book(conn)
            state = self._load(conn, event.instrument)
            order = self.execution._apply_event(conn, book, intent_id, event)
            state = self._assess(conn, book, state, clock)
            self._save(conn, state, event)
            return order

    def select_policy(self, instrument, policy, verification):
        with transaction(self.connect) as conn:
            self.execution._book(conn)
            state = self._load(conn, instrument)
            if state.exit_request:
                raise ValueError("cannot switch policy during exit")
            state = replace(state, policy=policy, verification=verification)
            self._save(conn, state, verification)

    def _actual(self, book, instrument, order_id):
        return Quantity(
            total(
                f.quantity.amount
                for f in book.fills.values()
                if f.instrument == instrument
                and f.account_id == self.account
                and f.order_id == order_id
                and f.side == "SELL"
            ),
            instrument.base,
        )

    def observe_stop(self, observation, clock, *, side=None, trigger=None, quantity=None):
        with transaction(self.connect) as conn:
            book = self.execution._book(conn)
            state = self._load(conn, observation.instrument)
            context = state.context
            if (
                observation.account_id != self.account
                or observation.order_id != context.stop_id
                or observation.cumulative_quantity.base != context.instrument.base
                or observation.cumulative_quantity.amount < ZERO
            ):
                raise ValueError("stop observation scope conflict")
            known = conn.execute(
                "SELECT evidence FROM lifecycle_audit WHERE account_id=%s AND instrument=%s "
                "AND evidence->>'source_id'=%s",
                (self.account, self._key(observation.instrument), observation.source_id),
            ).fetchall()
            for prior_row in known:
                if prior_row[0] != json.loads(observation.to_json()):
                    raise ValueError("stop observation source identity conflict")
                return state
            if observation.terminal != (
                observation.status in {"CANCELED", "FILLED", "REJECTED", "EXPIRED"}
            ):
                raise ValueError("invalid terminal stop evidence")
            prior = state.stop_observation
            if (
                prior
                and prior.terminal
                and observation.terminal
                and prior.status != observation.status
            ):
                raise ValueError("conflicting terminal stop evidence")
            if (
                prior
                and observation.observed_at >= prior.observed_at
                and observation.cumulative_quantity.amount < prior.cumulative_quantity.amount
            ):
                raise ValueError("stop cumulative fill regression")
            if prior and observation.observed_at < prior.observed_at:
                self._save(conn, state, observation)
                return state
            if prior and prior.terminal and not observation.terminal:
                self._save(conn, state, observation)
                return state
            actual = self._actual(book, context.instrument, context.stop_id)
            valid = False
            status = observation.status
            if status == "ACTIVE":
                target = assess_protection(book.snapshot, context, clock).target.amount
                if (
                    side != "SELL"
                    or trigger != context.trigger
                    or quantity != context.stop_quantity
                    or quantity is None
                    or quantity.amount > target
                    or observation.terminal
                ):
                    raise ValueError("stop parameters do not confirm legal protection")
                valid = observation.cumulative_quantity == actual
                status = "CONFIRMED" if valid else "UNKNOWN"
            elif status not in {
                "CANCEL_PENDING",
                "CANCELED",
                "FILLED",
                "REJECTED",
                "EXPIRED",
                "UNKNOWN",
            }:
                raise ValueError("unsupported stop status")
            if observation.terminal != (status in {"CANCELED", "FILLED", "REJECTED", "EXPIRED"}):
                raise ValueError("invalid terminal stop evidence")
            reconciled = None
            if observation.terminal and observation.cumulative_quantity == actual:
                self._operation(conn, book, "release", "lifecycle:" + context.stop_id, observation)
                reconciled = book.version
            if observation.status != "UNKNOWN":
                conn.execute(
                    "UPDATE lifecycle_command_status SET status='RESOLVED' "
                    "WHERE account_id=%s AND command_id=%s",
                    (self.account, context.stop_id),
                )
            context = replace(context, stop_status=status, confirmed_valid=valid)
            state = replace(
                state, context=context, stop_observation=observation, reconciled_version=reconciled
            )
            state = self._assess(conn, book, state, clock, create=False)
            self._save(conn, state, observation)
            return state

    def apply_stop_fill(self, fill, clock):
        with transaction(self.connect) as conn:
            book = self.execution._book(conn)
            state = self._load(conn, fill.instrument)
            if (
                fill.account_id != self.account
                or fill.order_id != state.context.stop_id
                or fill.side != "SELL"
            ):
                raise ValueError("stop fill scope conflict")
            self._operation(conn, book, "post_fill", fill)
            # Active coverage means outstanding quantity, not original requested quantity.
            original = next(
                c for c in self.commands_in(conn, fill.instrument) if c.command_id == fill.order_id
            )
            remaining = sub(
                original.quantity.amount, self._actual(book, fill.instrument, fill.order_id).amount
            )
            state = replace(
                state,
                context=replace(
                    state.context, stop_quantity=Quantity(remaining, fill.instrument.base)
                ),
                reconciled_version=None,
            )
            state = self._assess(conn, book, state, clock, create=False)
            self._save(conn, state, fill)
            return state

    def commands_in(self, conn, instrument):
        rows = conn.execute(
            "SELECT proposal FROM lifecycle_commands WHERE account_id=%s AND instrument=%s",
            (self.account, self._key(instrument)),
        ).fetchall()
        return [LifecycleCommand.from_json(json.dumps(row[0])) for row in rows]

    def request_exit(self, request, clock):
        with transaction(self.connect) as conn:
            book = self.execution._book(conn)
            state = self._load(conn, request.instrument)
            state = self._assess(conn, book, state, clock, create=False)
            if state.exit_request and state.exit_request != request:
                raise ValueError("another exit already owns instrument")
            observation = state.stop_observation
            actual = self._actual(book, request.instrument, state.context.stop_id)
            terminal_ok = bool(
                observation and observation.terminal and observation.cumulative_quantity == actual
            )
            if terminal_ok and state.reconciled_version is None:
                self._operation(
                    conn, book, "release", "lifecycle:" + state.context.stop_id, observation
                )
                state = replace(state, reconciled_version=book.version)
            exit_state = ExitState(
                self.account,
                state.context,
                state.context.protection.target,
                state.policy,
                state.verification,
                cumulative_filled=observation.cumulative_quantity if observation else None,
                actual_filled=actual,
                reconciled=terminal_ok,
                sell_status=state.sell_status,
            )
            proposals = request_exit(request, exit_state)
            for proposal in proposals:
                self._enqueue(conn, book, proposal)
                state = replace(state, exit_request=request)
                if proposal.kind == "CANCEL_STOP":
                    opened = state.serial_opened_at or clock.utc_now()
                    state = replace(
                        state,
                        context=replace(
                            state.context, stop_status="CANCEL_PENDING", confirmed_valid=False
                        ),
                        serial_opened_at=opened,
                        serial_deadline=opened + timedelta(seconds=5),
                    )
                    state = self._assess(conn, book, state, clock, create=False)
                else:
                    state = replace(state, sell_id=proposal.command_id, sell_status="PENDING")
            self._save(conn, state, request)
            return proposals

    def _claim_fake(self, command_id, clock):
        with transaction(self.connect) as conn:
            self.execution._book(conn)
            row = conn.execute(
                "UPDATE lifecycle_command_status SET status='UNKNOWN' WHERE account_id=%s "
                "AND command_id=%s AND status='PENDING' RETURNING 1",
                (self.account, command_id),
            ).fetchone()
            if row is None:
                return False
            proposal_row = conn.execute(
                "SELECT proposal FROM lifecycle_commands WHERE account_id=%s AND command_id=%s",
                (self.account, command_id),
            ).fetchone()
            proposal = LifecycleCommand.from_json(json.dumps(proposal_row[0]))
            state = self._load(conn, proposal.instrument)
            if proposal.kind in {"NATIVE_LINKED_EXIT", "MARKET_SELL"}:
                state = replace(state, sell_status="UNKNOWN")
            if proposal.kind in {"CREATE_STOP", "NATIVE_LINKED_EXIT"}:
                state = replace(
                    state,
                    context=replace(state.context, stop_status="UNKNOWN", confirmed_valid=False),
                )
            state = self._assess(
                conn, self.execution.ledger._replay(conn), state, clock, create=False
            )
            self._save(conn, state, proposal)
            return True

    def execute_fake(self, command_id, clock):
        """Synthetic atomic linked operation; unknown commands are never retried.

        Claim commits UNKNOWN before any simulated external effect. A storage failure
        after this point leaves an audited ambiguous command and its reservation.
        """
        if not self._claim_fake(command_id, clock):
            return False
        with transaction(self.connect) as conn:
            book = self.execution._book(conn)
            row = conn.execute(
                "SELECT proposal FROM lifecycle_commands WHERE account_id=%s AND command_id=%s",
                (self.account, command_id),
            ).fetchone()
            proposal = LifecycleCommand.from_json(json.dumps(row[0]))
            state = self._load(conn, proposal.instrument)
            state = self._assess(conn, book, state, clock, create=False)
            if proposal.kind in {"MARKET_SELL", "NATIVE_LINKED_EXIT"}:
                # Refresh ownership, fills and conservative fee commitments at execution.
                if proposal.quantity.amount > state.context.protection.target.amount:
                    raise ValueError("stale sell exceeds current net inventory")
                if state.sell_id != command_id or state.sell_status != "UNKNOWN":
                    raise ValueError("fake sell lost exclusive right")
                if proposal.kind == "NATIVE_LINKED_EXIT":
                    if (
                        state.context.stop_status != "UNKNOWN"
                        or state.context.stop_id != proposal.stop_id
                        or proposal.quantity != state.context.stop_quantity
                    ):
                        raise ValueError("linked fake requires matching active stop right")
                    order_id = proposal.stop_id
                else:
                    if (
                        state.context.stop_status not in {"CANCELED", "FILLED"}
                        or state.stop_observation is None
                        or not state.stop_observation.terminal
                        or state.stop_observation.cumulative_quantity
                        != self._actual(book, proposal.instrument, proposal.stop_id)
                    ):
                        raise ValueError("serial fake requires reconciled terminal stop")
                    order_id = command_id
                fill = Fill(
                    "synthetic:" + command_id,
                    self.account,
                    proposal.instrument,
                    order_id,
                    "SELL",
                    proposal.quantity,
                    state.context.trigger,
                    (),
                    clock.utc_now(),
                    "synthetic:" + command_id,
                    True,
                )
                self._operation(conn, book, "post_fill", fill)
                observation = OrderObservation(
                    "synthetic:terminal:" + command_id,
                    self.account,
                    proposal.instrument,
                    order_id,
                    self._actual(book, proposal.instrument, order_id),
                    "FILLED",
                    True,
                    clock.utc_now(),
                )
                self._operation(conn, book, "release", "lifecycle:" + order_id, observation)
                state = replace(state, sell_status="FILLED")
                if proposal.kind == "NATIVE_LINKED_EXIT":
                    state = replace(
                        state,
                        context=replace(
                            state.context,
                            stop_status="FILLED",
                            stop_quantity=Quantity(ZERO, proposal.instrument.base),
                            confirmed_valid=False,
                        ),
                        stop_observation=observation,
                        reconciled_version=book.version,
                    )
                state = self._assess(conn, book, state, clock, create=False)
                self._save(conn, state, fill)
            elif proposal.kind == "CANCEL_STOP":
                # This fake emits only ACK; a separate terminal observation + actual trades
                # is still mandatory before MARKET_SELL admission.
                observation = OrderObservation(
                    "synthetic:cancel-ack:" + command_id,
                    self.account,
                    proposal.instrument,
                    proposal.stop_id,
                    self._actual(book, proposal.instrument, proposal.stop_id),
                    "CANCEL_PENDING",
                    False,
                    clock.utc_now(),
                )
                # A delayed ACK cannot replace already observed terminal evidence.
                if state.stop_observation is not None and state.stop_observation.terminal:
                    self._save(conn, state, observation)
                else:
                    self._save(conn, replace(state, stop_observation=observation), observation)
            elif proposal.kind == "CREATE_STOP":
                if (
                    proposal.quantity.amount > state.context.protection.target.amount
                    or state.context.stop_id != command_id
                ):
                    raise ValueError("stale fake stop")
                state = replace(
                    state,
                    context=replace(state.context, stop_status="CONFIRMED", confirmed_valid=True),
                )
                state = self._assess(conn, book, state, clock, create=False)
                self._save(conn, state, proposal)
            else:
                raise ValueError("unsupported fake command")
            conn.execute(
                "UPDATE lifecycle_command_status SET status='RESOLVED' "
                "WHERE account_id=%s AND command_id=%s",
                (self.account, command_id),
            )
        return True
