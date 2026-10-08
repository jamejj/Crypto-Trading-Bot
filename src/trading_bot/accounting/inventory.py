"""FIFO inventory reduction preserves cost, risk and explicit dust."""

from dataclasses import replace

from trading_bot.domain.money import Money, Quantity

from .arithmetic import ZERO, proportional, sub


def consume(lots, instrument, amount, protected=None):
    remaining = amount
    protected = protected or {}
    result = []
    for lot in lots:
        if lot.instrument != instrument or remaining == ZERO:
            result.append(lot)
            continue
        available = sub(lot.quantity.amount, protected.get(lot.lot_id, ZERO))
        used = min(max(ZERO, available), remaining)
        left = sub(lot.quantity.amount, used)
        remaining = sub(remaining, used)
        if left:
            result.append(
                replace(
                    lot,
                    quantity=Quantity(left, instrument.base),
                    cost=Money(
                        proportional(lot.cost.amount, left, lot.quantity.amount), lot.cost.currency
                    ),
                    risk=Money(
                        proportional(lot.risk.amount, left, lot.quantity.amount), lot.risk.currency
                    ),
                )
            )
    if remaining:
        raise ValueError("available inventory insufficient")
    return result
