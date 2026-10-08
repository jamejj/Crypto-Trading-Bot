"""Encumbrances constrain spendability without duplicating owned assets."""

from trading_bot.domain.money import Money, Quantity

from .arithmetic import ZERO, total


def commitments(reservations, currency, exclude_order=None):
    values = []
    for item in reservations:
        if item.status != "PENDING" or item.order_id == exclude_order:
            continue
        if item.side == "BUY" and item.cash.currency == currency:
            values.append(item.cash.amount)
        if item.side == "SELL" and item.quantity.base == currency:
            values.append(item.quantity.amount)
        values.extend(m.amount for m in item.fee_buffers if m.currency == currency)
    return total(values)


def validate(item, account, quote):
    if (
        item.account_id != account
        or item.instrument is None
        or not item.order_id
        or item.side not in {"BUY", "SELL"}
        or item.quantity is None
        or item.quantity.base != item.instrument.base
        or item.cash.currency != quote
        or item.instrument.quote != quote
        or item.risk.currency != quote
        or item.status != "PENDING"
    ):
        raise ValueError("reservation requires scoped account/order/instrument and asset units")
    if (
        item.quantity.amount <= ZERO
        or item.cash.amount < ZERO
        or item.risk.amount < ZERO
        or any(m.amount <= ZERO for m in item.fee_buffers)
    ):
        raise ValueError(
            "reservation values must be nonnegative with positive quantity and fee buffers"
        )
    if len({m.currency for m in item.fee_buffers}) != len(item.fee_buffers):
        raise ValueError("fee buffer currency must be unique")
    if item.side == "SELL" and item.cash.amount != ZERO:
        raise ValueError("SELL reservation cannot commit cash principal")


def empty(item):
    from dataclasses import replace

    return replace(
        item,
        cash=Money(ZERO, item.cash.currency),
        quantity=Quantity(ZERO, item.quantity.base),
        risk=Money(ZERO, item.risk.currency),
        fee_buffers=(),
        status="RELEASED",
    )
