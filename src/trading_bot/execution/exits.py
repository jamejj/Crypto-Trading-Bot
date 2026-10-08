"""Explicit, fake-only exit policies; synthetic verification is never live V evidence."""

from dataclasses import dataclass

from trading_bot.accounting.arithmetic import ZERO
from trading_bot.domain.money import Quantity
from trading_bot.domain.records import ExitRequest, InstrumentId
from trading_bot.domain.serialization import Record
from trading_bot.execution.protection import ProtectionContext, command

POLICIES = {"SERIAL_CANCEL_THEN_MARKET_VERIFIED", "NATIVE_LINKED_VERIFIED"}


@dataclass(frozen=True)
class SyntheticExitVerification(Record):
    account_id: str
    instrument: InstrumentId
    policy: str
    artifact: str
    verified: bool


@dataclass(frozen=True)
class ExitState(Record):
    account_id: str
    context: ProtectionContext
    remaining: Quantity
    policy: str = "DISABLED"
    verification: SyntheticExitVerification | None = None
    stop_status: str = ""
    cumulative_filled: Quantity | None = None
    actual_filled: Quantity | None = None
    reconciled: bool = False
    sell_status: str = "NONE"


def request_exit(request: ExitRequest, state: ExitState):
    context, evidence = state.context, state.verification
    if (
        request.instrument != context.instrument
        or request.scope != state.account_id
        or request.quantity.base != context.instrument.base
        or state.remaining.base != context.instrument.base
    ):
        raise ValueError("exit scope conflict")
    if (
        state.policy not in POLICIES
        or evidence is None
        or not evidence.verified
        or evidence.account_id != state.account_id
        or evidence.instrument != request.instrument
        or evidence.policy != state.policy
        or not evidence.artifact.startswith("synthetic:")
    ):
        return []
    if state.sell_status != "NONE" or state.remaining.amount <= ZERO:
        return []
    status = state.stop_status or context.stop_status
    if status in {"UNKNOWN", "PENDING", "CANCEL_PENDING"}:
        return []
    amount = min(request.quantity.amount, state.remaining.amount)
    if amount <= ZERO:
        return []
    if state.policy == "NATIVE_LINKED_VERIFIED" and status == "CONFIRMED":
        if context.stop_quantity != state.remaining or request.quantity != state.remaining:
            return []
        kind = "NATIVE_LINKED_EXIT"
    elif state.policy == "SERIAL_CANCEL_THEN_MARKET_VERIFIED" and status == "CONFIRMED":
        kind = "CANCEL_STOP"
    elif state.policy == "SERIAL_CANCEL_THEN_MARKET_VERIFIED" and status in {
        "CANCELED",
        "FILLED",
        "NONE",
    }:
        if (
            not state.reconciled
            or state.cumulative_filled is None
            or state.actual_filled is None
            or state.cumulative_filled != state.actual_filled
        ):
            return []
        kind = "MARKET_SELL"
    else:
        return []
    return [
        command(request.request_id, kind, request.instrument, amount, context.stop_id, state.policy)
    ]
