"""Offline protection decisions. Pending/ambiguous orders never count as coverage."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trading_bot.accounting.arithmetic import ZERO, sub, total
from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import CommandProposal, InstrumentId, LotDeadline, ProtectionState
from trading_bot.domain.serialization import Record, exact_add
from trading_bot.execution.reducer import TERMINAL


@dataclass(frozen=True)
class LifecycleCommand(CommandProposal):
    instrument: InstrumentId
    quantity: Quantity
    stop_id: str
    policy: str
    trigger: Money | None = None


def command(identity, kind, instrument, amount, stop_id="", policy="", trigger=None):
    quantity = Quantity(amount, instrument.base)
    payload = "|".join(
        (
            identity,
            kind,
            instrument.to_json(),
            quantity.to_json(),
            stop_id,
            policy,
            trigger.to_json() if trigger else "",
        )
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return LifecycleCommand(
        digest, identity, kind, digest, instrument, quantity, stop_id, policy, trigger
    )


@dataclass(frozen=True)
class ProtectionContext(Record):
    instrument: InstrumentId
    trigger: Money
    minimum: Decimal
    protection: ProtectionState | None = None
    stop_id: str = ""
    stop_status: str = "NONE"
    stop_quantity: Quantity | None = None
    confirmed_valid: bool = False

    def __post_init__(self):
        super().__post_init__()
        if (
            self.minimum <= ZERO
            or self.trigger.amount <= ZERO
            or self.trigger.currency != self.instrument.quote
        ):
            raise ValueError("invalid protection rules")
        if self.stop_quantity and (
            self.stop_quantity.base != self.instrument.base or self.stop_quantity.amount < ZERO
        ):
            raise ValueError("invalid stop quantity")
        if self.protection and self.protection.instrument != self.instrument:
            raise ValueError("protection instrument conflict")


class ProtectionClock:
    """Bind durable UTC deadlines once to this process's monotonic clock.

    On restart construct a fresh wrapper; expired UTC deadlines bind immediately.
    A wall-clock jump after binding cannot extend the exposure window.
    """

    def __init__(self, clock):
        self.clock = clock
        self._due = {}

    def utc_now(self):
        return self.clock.utc_now()

    def overdue(self, deadline: datetime):
        if deadline not in self._due:
            delta = max(timedelta(), deadline - self.utc_now())
            seconds = Decimal(delta.days * 86400 + delta.seconds)
            seconds = exact_add(seconds, Decimal(delta.microseconds).scaleb(-6))
            self._due[deadline] = exact_add(self.clock.monotonic_now(), seconds)
        return self.clock.monotonic_now() >= self._due[deadline]


def terminal_partial(order):
    return (
        order.status in TERMINAL
        and order.filled is not None
        and ZERO < order.filled.amount < order.target.amount
    )


def assess_protection(inventory, protection: ProtectionContext, clock) -> ProtectionState:
    instrument = protection.instrument
    lots = [lot for lot in inventory.inventory if lot.instrument == instrument]
    gross = total(lot.quantity.amount for lot in lots)
    pending_fee = total(
        m.amount for m in inventory.fee_commitments if m.currency == instrument.base
    )
    target = max(ZERO, sub(gross, pending_fee))
    confirmed = protection.stop_status == "CONFIRMED" and protection.confirmed_valid
    conflict = bool(
        protection.stop_quantity
        and protection.stop_quantity.amount > target
        and protection.stop_status not in {"NONE", "CANCELED", "FILLED"}
    )
    covered = (
        protection.stop_quantity.amount
        if confirmed and protection.stop_quantity and not conflict
        else ZERO
    )
    old = (
        {d.lot_id: d.deadline for d in protection.protection.lot_deadlines}
        if protection.protection
        else {}
    )
    deadlines = []
    available, coverage = target, covered
    for lot in lots:
        amount = min(lot.quantity.amount, available)
        available = sub(available, amount)
        used = min(coverage, amount)
        coverage = sub(coverage, used)
        uncovered = sub(amount, used)
        if uncovered > ZERO:
            deadline = old.get(lot.lot_id, clock.utc_now() + timedelta(seconds=5))
            deadlines.append(
                LotDeadline(lot.lot_id, Quantity(uncovered, instrument.base), deadline)
            )
    # Bind every timer even when an earlier lot is already overdue.
    due = tuple(clock.overdue(d.deadline) for d in deadlines)
    overdue = any(due)
    status = (
        "CONFLICT"
        if conflict
        else "OVERDUE"
        if overdue
        else "UNKNOWN"
        if protection.stop_status == "UNKNOWN"
        else "UNCOVERED"
        if target > covered
        else "CONFIRMED"
        if target
        else "EMPTY"
    )
    return ProtectionState(
        instrument,
        Quantity(target, instrument.base),
        Quantity(covered, instrument.base),
        Quantity(sub(target, covered), instrument.base),
        tuple(deadlines),
        (protection.stop_id,) if protection.stop_id else (),
        status,
    )


def propose_stop(context: ProtectionContext, account_id: str) -> list[LifecycleCommand]:
    state = context.protection
    # Enlarging/replacing a confirmed stop must use the selected exit/replacement policy.
    if state is None or context.stop_status != "NONE" or state.target.amount < context.minimum:
        return []
    return [
        command(
            account_id
            + ":"
            + context.instrument.market
            + ":"
            + "|".join(d.lot_id for d in state.lot_deadlines),
            "CREATE_STOP",
            context.instrument,
            state.target.amount,
            trigger=context.trigger,
        )
    ]
