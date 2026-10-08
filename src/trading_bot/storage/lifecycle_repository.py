"""Offline durable protection/exit coordinator, serialized by the P02 account lock.

Every fake effect requires P03.5 ownership and independent fencing; no live adapter.
Every fake sell right is a P02 SELL reservation; linked exit shares its stop right.
"""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from trading_bot.accounting.arithmetic import ZERO, sub, total
from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import ExitRequest, Fill, OrderObservation, Reservation
from trading_bot.domain.serialization import Record
from trading_bot.execution.absence import SyntheticAbsenceEvidence
from trading_bot.execution.exits import ExitState, SyntheticExitVerification, request_exit
from trading_bot.execution.ownership import require_writer
from trading_bot.execution.protection import (
    LifecycleCommand,
    ProtectionContext,
    assess_protection,
    command,
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
    replacing: bool = False
    incident: str = ""
    absence: SyntheticAbsenceEvidence | None = None


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

    def get_command(self, command_id):
        with transaction(self.connect) as conn:
            row = conn.execute(
                "SELECT c.proposal,s.status FROM lifecycle_commands c "
                "JOIN lifecycle_command_status s USING(account_id,command_id) "
                "WHERE c.account_id=%s AND c.command_id=%s",
                (self.account, command_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown lifecycle command")
            return LifecycleCommand.from_json(json.dumps(row[0])), row[1]

    def _operation(self, conn, book, method, *args):
        version, count = book.version, len(book.entries)
        getattr(book, method)(*args)
        self.execution._persist(conn, book, version, count)

    def _reserve(self, conn, book, proposal, quantity=None):
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
            quantity or proposal.quantity,
        )
        # P02 checks all account base commitments across markets, not just this instrument.
        self._operation(conn, book, "reserve", item, book.version)

    def _enqueue(self, conn, book, proposal, *, increment=None):
        if proposal.kind in {"CREATE_STOP", "MARKET_SELL"}:
            self._reserve(conn, book, proposal)
        if increment is not None:
            self._reserve(conn, book, proposal, increment)
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
        if (
            protection.uncovered.amount == ZERO
            and state.context.stop_status == "CONFIRMED"
            and not state.replacing
        ):
            # Completed windows remain in immutable audit; they cannot expire a later window.
            state = replace(state, serial_opened_at=None, serial_deadline=None)
        proposals = (
            propose_stop(state.context, self.account)
            if create and not state.exit_request and protection.status != "OVERDUE"
            else []
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

    def _verified(self, state):
        evidence = state.verification
        return bool(
            evidence
            and evidence.verified
            and evidence.account_id == self.account
            and evidence.instrument == state.context.instrument
            and evidence.policy == state.policy
            and evidence.artifact.startswith("synthetic:")
            and state.policy in {"SERIAL_CANCEL_THEN_MARKET_VERIFIED", "NATIVE_LINKED_VERIFIED"}
        )

    def _cancel(self, conn, book, state, clock, identity, quantity=None):
        protection = state.context.protection
        opened = state.serial_opened_at or clock.utc_now()
        deadline = min(
            (d.deadline for d in protection.lot_deadlines), default=opened + timedelta(seconds=5)
        )
        deadline = min(deadline, state.serial_deadline or deadline)
        proposal = command(
            identity,
            "CANCEL_STOP",
            state.context.instrument,
            quantity.amount if quantity is not None else protection.target.amount,
            state.context.stop_id,
            state.policy,
        )
        self._enqueue(conn, book, proposal)
        state = replace(
            state,
            context=replace(state.context, stop_status="CANCEL_PENDING", confirmed_valid=False),
            serial_opened_at=opened,
            serial_deadline=deadline,
        )
        return self._assess(conn, book, state, clock, create=False)

    def _terminal(self, book, state):
        observation = state.stop_observation
        return bool(
            observation
            and observation.terminal
            and observation.order_id == state.context.stop_id
            and observation.cumulative_quantity
            == self._actual(book, state.context.instrument, state.context.stop_id)
        )

    def _manage(self, conn, book, state, clock):
        protection = state.context.protection
        overdue = protection.uncovered.amount > ZERO and (
            any(clock.overdue(d.deadline) for d in protection.lot_deadlines)
            or bool(
                state.replacing and state.serial_deadline and clock.overdue(state.serial_deadline)
            )
        )
        if state.sell_status == "FILLED" and protection.target.amount > ZERO:
            return replace(state, incident="BLOCKED_RESIDUAL_AFTER_EXIT")
        if overdue and protection.target.amount > ZERO and not state.exit_request:
            deadline = min(
                (d.deadline for d in protection.lot_deadlines),
                default=state.serial_deadline or clock.utc_now(),
            )
            request = ExitRequest(
                "emergency:"
                + self.account
                + ":"
                + state.context.instrument.market
                + ":"
                + deadline.isoformat(),
                state.context.instrument,
                protection.target,
                "unprotected_deadline",
                self.account,
                deadline,
            )
            state = replace(state, exit_request=request, incident="EMERGENCY_EXIT_REQUIRED")
        if state.exit_request:
            return self._exit(conn, book, state, state.exit_request, clock)[0]
        if (
            not state.replacing
            and state.context.stop_status == "CONFIRMED"
            and protection.uncovered.amount > ZERO
        ):
            if (
                not self._verified(state)
                or not state.verification.replacement_verified
                or not state.verification.replacement_artifact.startswith("synthetic:")
            ):
                return replace(state, incident="BLOCKED_REPLACEMENT_CAPABILITY")
            if state.policy == "NATIVE_LINKED_VERIFIED":
                proposal = command(
                    "replace:" + state.context.stop_id,
                    "NATIVE_REPLACE_STOP",
                    state.context.instrument,
                    protection.target.amount,
                    state.context.stop_id,
                    state.policy,
                    state.context.trigger,
                )
                if protection.target.amount < state.context.minimum:
                    return replace(state, incident="BLOCKED_REPLACEMENT_MINIMUM")
                amount = max(
                    ZERO, sub(protection.target.amount, state.context.stop_quantity.amount)
                )
                increment = (
                    Quantity(amount, state.context.instrument.base) if amount > ZERO else None
                )
                self._enqueue(conn, book, proposal, increment=increment)
                return replace(state, replacing=True)

            state = replace(state, replacing=True)
            return self._cancel(conn, book, state, clock, "replace:" + state.context.stop_id)
        if state.replacing and self._terminal(book, state):
            if protection.target.amount < state.context.minimum:
                return replace(state, incident="BLOCKED_REPLACEMENT_MINIMUM")
            proposal = command(
                "replace:" + state.context.stop_id,
                "CREATE_STOP",
                state.context.instrument,
                protection.target.amount,
                trigger=state.context.trigger,
            )
            self._enqueue(conn, book, proposal)
            return replace(
                state,
                replacing=False,
                stop_observation=None,
                reconciled_version=None,
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
            state = self._manage(conn, book, state, clock)
            self._save(conn, state)
            return state

    def apply_entry_event(self, intent_id, event, clock):
        with transaction(self.connect) as conn:
            book = self.execution._book(conn)
            state = self._load(conn, event.instrument)
            order = self.execution._apply_event(conn, book, intent_id, event)
            state = self._assess(conn, book, state, clock)
            state = self._manage(conn, book, state, clock)
            self._save(conn, state, event)
            return order

    def select_policy(self, instrument, policy, verification):
        with transaction(self.connect) as conn:
            self.execution._book(conn)
            state = self._load(conn, instrument)
            if state.exit_request or state.replacing:
                raise ValueError("cannot switch policy during exit")
            if policy == "NATIVE_LINKED_VERIFIED" and not (
                verification
                and verification.replacement_verified
                and verification.replacement_artifact.startswith("synthetic:")
                and verification.native_atomic_emergency_verified
                and verification.emergency_artifact.startswith("synthetic:")
                and verification.absent_stop_market_verified
            ):
                raise ValueError("native protection management capability incomplete")
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

    def _absence_valid(self, conn, book, state, evidence, clock, *, executing=False):
        if evidence is None or state.context.stop_status != "NONE" or state.context.stop_id:
            return False
        snapshot = evidence.snapshot
        if (
            not self._verified(state)
            or not state.verification.absent_stop_market_verified
            or not state.verification.emergency_artifact.startswith("synthetic:")
            or evidence.account_id != self.account
            or evidence.instrument != state.context.instrument
            or evidence.ledger_version != book.version
            or evidence.quantity != state.context.protection.target
            or snapshot.source_id != state.verification.venue_source
            or snapshot.account_id != self.account
            or snapshot.base != state.context.instrument.base
            or snapshot.sequence < 0
            or not snapshot.orders_complete
            or not snapshot.trades_complete
            or snapshot.observed_at > clock.utc_now()
            or clock.utc_now() - snapshot.observed_at > timedelta(seconds=1)
        ):
            return False
        if state.absence and (
            snapshot.source_id != state.absence.snapshot.source_id
            or snapshot.sequence < state.absence.snapshot.sequence
        ):
            return False
        if any(
            o.account_id != self.account
            or o.instrument.base != snapshot.base
            or o.side not in {"BUY", "SELL"}
            or o.status
            not in {"ACTIVE", "CANCEL_PENDING", "CANCELED", "FILLED", "REJECTED", "EXPIRED"}
            or (o.side == "SELL" and o.status not in {"CANCELED", "FILLED", "REJECTED", "EXPIRED"})
            for o in snapshot.orders
        ):
            return False
        local = tuple(
            f
            for f in book.fills.values()
            if f.account_id == self.account and f.instrument.base == snapshot.base
        )
        if len(
            {(f.account_id, f.instrument, f.trade_id or f.source_id) for f in snapshot.trades}
        ) != len(snapshot.trades) or set(snapshot.trades) != set(local):
            return False
        commands = conn.execute(
            "SELECT c.command_id,c.proposal,s.status FROM lifecycle_commands c "
            "JOIN lifecycle_command_status s USING(account_id,command_id) WHERE c.account_id=%s",
            (self.account,),
        ).fetchall()
        for command_id, payload, status in commands:
            proposal = LifecycleCommand.from_json(json.dumps(payload))
            if proposal.instrument.base == snapshot.base and status != "RESOLVED":
                if not executing or command_id != state.sell_id or status != "UNKNOWN":
                    return False
        if conn.execute(
            "SELECT 1 FROM execution_outbox WHERE account_id=%s "
            "AND state IN ('DISPATCHING','SUBMISSION_UNKNOWN')",
            (self.account,),
        ).fetchone():
            return False
        for reservation in book.snapshot.reservations:
            if (
                reservation.side == "SELL"
                and reservation.instrument.base == snapshot.base
                and reservation.status != "RELEASED"
            ):
                if not executing or reservation.order_id != state.sell_id:
                    return False
        return True

    def _exit(self, conn, book, state, request, clock, absence=None):
        if state.exit_request and state.exit_request != request:
            raise ValueError("another exit already owns instrument")
        if (
            request.instrument != state.context.instrument
            or request.scope != self.account
            or request.quantity.base != state.context.instrument.base
            or request.quantity.amount <= ZERO
        ):
            raise ValueError("exit scope conflict")
        state = replace(state, exit_request=request)
        if state.context.protection.target.amount == ZERO:
            return state, []
        effective_request = (
            replace(request, quantity=state.context.protection.target)
            if request.reason == "unprotected_deadline"
            else request
        )
        if (
            min(effective_request.quantity.amount, state.context.protection.target.amount)
            < state.context.exit_minimum
        ):
            return replace(state, incident="BLOCKED_BELOW_EXIT_MINIMUM"), []
        observation = state.stop_observation
        actual = self._actual(book, request.instrument, state.context.stop_id)
        terminal_ok = self._terminal(book, state)
        if terminal_ok and state.reconciled_version is None:
            self._operation(
                conn, book, "release", "lifecycle:" + state.context.stop_id, observation
            )
            state = replace(state, reconciled_version=book.version)
        if state.context.stop_status == "NONE":
            if state.sell_status != "NONE" or not self._absence_valid(
                conn, book, state, absence, clock
            ):
                return replace(state, incident="BLOCKED_ABSENCE_EVIDENCE"), []
            proposal = command(
                request.request_id,
                "MARKET_SELL",
                request.instrument,
                min(effective_request.quantity.amount, state.context.protection.target.amount),
                policy=state.policy,
            )
            proposals = [proposal]
            state = replace(state, absence=absence)
        elif (
            state.policy == "NATIVE_LINKED_VERIFIED"
            and self._verified(state)
            and state.verification.native_atomic_emergency_verified
            and state.verification.emergency_artifact.startswith("synthetic:")
            and request.reason == "unprotected_deadline"
            and state.sell_status == "NONE"
            and state.context.stop_status == "CONFIRMED"
            and not state.replacing
        ):
            proposal = command(
                request.request_id,
                "NATIVE_EMERGENCY_EXIT",
                request.instrument,
                state.context.protection.target.amount,
                state.context.stop_id,
                state.policy,
            )
            proposals = [proposal]
        else:
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
            proposals = request_exit(effective_request, exit_state)
        for proposal in proposals:
            if proposal.kind == "CANCEL_STOP":
                state = self._cancel(
                    conn, book, state, clock, request.request_id, proposal.quantity
                )
            else:
                increment = None
                if proposal.kind == "NATIVE_EMERGENCY_EXIT":
                    amount = max(
                        ZERO, sub(proposal.quantity.amount, state.context.stop_quantity.amount)
                    )
                    increment = (
                        Quantity(amount, proposal.instrument.base) if amount > ZERO else None
                    )
                self._enqueue(conn, book, proposal, increment=increment)
                state = replace(state, sell_id=proposal.command_id, sell_status="PENDING")
        return state, proposals

    def request_exit(self, request, clock, *, absence=None):
        with transaction(self.connect) as conn:
            book = self.execution._book(conn)
            state = self._assess(
                conn, book, self._load(conn, request.instrument), clock, create=False
            )
            state, proposals = self._exit(conn, book, state, request, clock, absence)
            self._save(conn, state, absence or request)
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
            if proposal.kind in {"NATIVE_LINKED_EXIT", "MARKET_SELL", "NATIVE_EMERGENCY_EXIT"}:
                state = replace(state, sell_status="UNKNOWN")
            if proposal.kind in {
                "CREATE_STOP",
                "NATIVE_LINKED_EXIT",
                "NATIVE_REPLACE_STOP",
                "NATIVE_EMERGENCY_EXIT",
            }:
                state = replace(
                    state,
                    context=replace(state.context, stop_status="UNKNOWN", confirmed_valid=False),
                )
            state = self._assess(
                conn, self.execution.ledger._replay(conn), state, clock, create=False
            )
            self._save(conn, state, proposal)
            return True

    def _native_management(self, conn, book, state, proposal, clock):
        if (
            state.context.stop_id != proposal.stop_id
            or state.context.stop_status != "UNKNOWN"
            or proposal.quantity != state.context.protection.target
        ):
            raise ValueError("stale native management quantity or stop right")
        replacing = proposal.kind == "NATIVE_REPLACE_STOP"
        if replacing and proposal.quantity.amount < state.context.minimum:
            raise ValueError("native replacement below protection minimum")
        if replacing and (
            not state.replacing
            or (state.serial_deadline and clock.overdue(state.serial_deadline))
            or any(clock.overdue(d.deadline) for d in state.context.protection.lot_deadlines)
        ):
            raise ValueError("native replacement deadline expired")
        evidence = state.verification
        if not self._verified(state) or not (
            evidence.replacement_verified
            if replacing
            else evidence.native_atomic_emergency_verified
        ):
            raise ValueError("native management capability absent")
        actual = self._actual(book, proposal.instrument, proposal.stop_id)
        terminal = OrderObservation(
            "synthetic:atomic-retire:" + proposal.command_id,
            self.account,
            proposal.instrument,
            proposal.stop_id,
            actual,
            "CANCELED",
            True,
            clock.utc_now(),
        )
        self._operation(conn, book, "release", "lifecycle:" + proposal.stop_id, terminal)
        increment_terminal = replace(
            terminal,
            source_id="synthetic:increment:" + proposal.command_id,
            order_id=proposal.command_id,
            cumulative_quantity=Quantity(ZERO, proposal.instrument.base),
        )
        if "lifecycle:" + proposal.command_id in book.reservations:
            self._operation(
                conn, book, "release", "lifecycle:" + proposal.command_id, increment_terminal
            )
        kind = "CREATE_STOP" if replacing else "MARKET_SELL"
        child = command(
            "synthetic:atomic:" + proposal.command_id,
            kind,
            proposal.instrument,
            proposal.quantity.amount,
            trigger=state.context.trigger if replacing else None,
        )
        self._enqueue(conn, book, child)
        conn.execute(
            "UPDATE lifecycle_command_status SET status='RESOLVED' "
            "WHERE account_id=%s AND command_id=%s",
            (self.account, child.command_id),
        )
        if replacing:
            state = replace(
                state,
                replacing=False,
                stop_observation=None,
                reconciled_version=None,
                context=replace(
                    state.context,
                    stop_id=child.command_id,
                    stop_quantity=child.quantity,
                    stop_status="CONFIRMED",
                    confirmed_valid=True,
                ),
            )
        else:
            fill = Fill(
                "synthetic:" + proposal.command_id,
                self.account,
                proposal.instrument,
                child.command_id,
                "SELL",
                child.quantity,
                state.context.trigger,
                (),
                clock.utc_now(),
                "synthetic:" + proposal.command_id,
                True,
            )
            self._operation(conn, book, "post_fill", fill)
            sell_terminal = replace(
                terminal,
                order_id=child.command_id,
                source_id="synthetic:atomic-sold:" + proposal.command_id,
                cumulative_quantity=child.quantity,
                status="FILLED",
            )
            self._operation(conn, book, "release", "lifecycle:" + child.command_id, sell_terminal)
            state = replace(
                state,
                sell_status="FILLED",
                stop_observation=terminal,
                context=replace(state.context, stop_status="CANCELED", confirmed_valid=False),
                reconciled_version=book.version,
            )
        return self._assess(conn, book, state, clock, create=False)

    def execute_fake(self, command_id, clock, *, absence=None, writer=None):
        require_writer(writer)
        return writer.send(
            self.account, lambda: self._execute_fake(command_id, clock, absence=absence)
        )

    def _execute_fake(self, command_id, clock, *, absence=None):
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
            if proposal.kind in {"NATIVE_REPLACE_STOP", "NATIVE_EMERGENCY_EXIT"}:
                state = self._native_management(conn, book, state, proposal, clock)
                self._save(conn, state, proposal)
            elif proposal.kind in {"MARKET_SELL", "NATIVE_LINKED_EXIT"}:
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
                    if state.context.stop_status == "NONE":
                        if not self._absence_valid(
                            conn, book, state, absence, clock, executing=True
                        ):
                            raise ValueError(
                                "fresh positive absence evidence required for fake sell"
                            )
                    elif (
                        state.context.stop_status
                        not in {"CANCELED", "FILLED", "REJECTED", "EXPIRED"}
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
                if proposal.stop_id != state.context.stop_id or (
                    state.stop_observation is not None and state.stop_observation.terminal
                ):
                    self._save(conn, state, observation)
                else:
                    self._save(conn, replace(state, stop_observation=observation), observation)
            elif proposal.kind == "CREATE_STOP":
                if any(clock.overdue(d.deadline) for d in state.context.protection.lot_deadlines):
                    raise ValueError("stop protection deadline expired")
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
