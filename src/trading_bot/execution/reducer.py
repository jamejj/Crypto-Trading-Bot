"""Monotone order evidence projection; economic truth comes only from scoped trades."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from trading_bot.accounting.arithmetic import total
from trading_bot.domain.money import Quantity
from trading_bot.domain.records import Fill, InstrumentId, OrderObservation
from trading_bot.domain.serialization import Record

TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
RANK = {"SUBMITTED": 0, "ACKNOWLEDGED": 1, "ACTIVE": 2, "PARTIALLY_FILLED": 3, "CANCEL_PENDING": 4}


@dataclass(frozen=True)
class OrderState(Record):
    intent_id: str
    account_id: str
    instrument: InstrumentId
    order_id: str
    side: str
    target: Quantity
    status: str = "SUBMITTED"
    observations: tuple[OrderObservation, ...] = ()
    filled: Quantity | None = None
    newest_observation: datetime | None = None


def apply(state, event, ledger):
    if (event.account_id, event.instrument, event.order_id) != (
        state.account_id,
        state.instrument,
        state.order_id,
    ):
        raise ValueError("event order scope conflict")
    if isinstance(event, Fill):
        if event.side != state.side:
            raise ValueError("fill side scope conflict")
        known = (event.account_id, event.instrument, event.trade_id) in ledger.fills
        prior_amount = total(
            f.quantity.amount
            for f in ledger.fills.values()
            if (f.account_id, f.instrument, f.order_id)
            == (state.account_id, state.instrument, state.order_id)
        )
        from trading_bot.domain.serialization import exact_add

        if not known and exact_add(prior_amount, event.quantity.amount) > state.target.amount:
            raise ValueError("fill exceeds order target")
        # P02 owns economic dedup, fee corrections and inventory. Source ID is not a trade ID.
        ledger.post_fill(event)
        amount = total(
            f.quantity.amount
            for f in ledger.fills.values()
            if (f.account_id, f.instrument, f.order_id)
            == (state.account_id, state.instrument, state.order_id)
        )
        status = state.status
        if amount >= state.target.amount:
            status = "FILLED"
        elif status not in TERMINAL and status != "CANCEL_PENDING":
            status = "PARTIALLY_FILLED"
        return replace(state, status=status, filled=Quantity(amount, state.instrument.base))
    if not isinstance(event, OrderObservation):
        raise TypeError("unsupported execution evidence")
    if (
        event.status not in RANK
        and event.status not in TERMINAL
        or event.terminal != (event.status in TERMINAL)
        or event.cumulative_quantity.base != state.instrument.base
        or event.cumulative_quantity.amount < Decimal("0")
        or event.cumulative_quantity.amount > state.target.amount
    ):
        raise ValueError("invalid order status evidence")
    for prior in state.observations:
        if prior.source_id == event.source_id:
            if prior == event:
                return state
            raise ValueError("observation source identity conflict")
    observations = state.observations + (event,)
    if state.newest_observation is not None and event.observed_at < state.newest_observation:
        return replace(state, observations=observations)
    status = state.status
    if status not in TERMINAL:
        if event.terminal or RANK[event.status] > RANK.get(status, 0):
            status = event.status
    # No inferred fill and no reserve release, even from a terminal cancel ACK.
    return replace(
        state, status=status, observations=observations, newest_observation=event.observed_at
    )
