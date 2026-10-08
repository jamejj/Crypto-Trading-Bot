"""Conservative executable-bid NAV, including liabilities and only residual sale costs."""

from dataclasses import replace

from trading_bot.domain.money import Money

from .arithmetic import ZERO, div, mul, sub, total


def value(snapshot, market, cost_estimate):
    """Markets/costs are paired tuples; costs contain only residual costs after bid execution.

    sizing_cost in this accounting view is the explicit residual sale fee/shortfall,
    never a trigger-relative sizing percentile that already includes spread.
    """
    quote = snapshot.nav.currency
    markets = (market,) if hasattr(market, "instrument") else tuple(market)
    costs = (cost_estimate,) if hasattr(cost_estimate, "sizing_cost") else tuple(cost_estimate)
    if len(markets) != len(costs):
        raise ValueError("one residual cost estimate required per liquidation market")
    marks = {}
    for item, cost in zip(markets, costs, strict=True):
        if item.instrument.quote != quote or cost.sizing_cost.currency != quote:
            raise ValueError("valuation quote currency mismatch")
        if cost.basis != "REMAINING_AFTER_BIDS":
            raise ValueError("valuation costs must exclude costs already contained in bids")
        if cost.sizing_cost.amount < ZERO:
            raise ValueError("negative liquidation cost")
        if item.instrument.base in marks:
            raise ValueError("ambiguous asset liquidation market")
        marks[item.instrument.base] = (item, cost)
    assets = {m.currency: m.amount for m in snapshot.balances}
    for fee in snapshot.fee_commitments:
        assets[fee.currency] = sub(assets.get(fee.currency, ZERO), fee.amount)
    amounts = [assets.get(quote, ZERO)]
    reasons = []
    for currency, amount in assets.items():
        if currency == quote or amount == ZERO:
            continue
        if currency not in marks:
            reasons.append("UNPRICED:" + currency)
            continue
        book, cost = marks[currency]
        reasons.extend(book.uncertainty_reasons)
        reasons.extend(cost.uncertainty_reasons)
        remaining = amount
        proceeds = ZERO
        if amount < ZERO:
            reasons.append("UNPRICED_LIABILITY:" + currency)
            continue
        previous = None
        for price, quantity in book.bids:
            if (
                price.currency != quote
                or quantity.base != currency
                or price.amount <= ZERO
                or (previous is not None and price.amount > previous)
            ):
                raise ValueError("invalid executable bid book")
            previous = price.amount
            used = min(remaining, quantity.amount)
            proceeds = total((proceeds, mul(used, price.amount)))
            remaining = sub(remaining, used)
        if remaining:
            reasons.append("INSUFFICIENT_DEPTH:" + currency)
        amounts.append(sub(proceeds, cost.sizing_cost.amount))
    equity = total(amounts)
    if snapshot.units:
        nav = Money(div(equity, snapshot.units), quote)
    else:
        nav = snapshot.nav
        if equity:
            reasons.append("EQUITY_WITHOUT_UNITS")
    hwm = snapshot.high_water_mark
    if not reasons and nav.amount > hwm.amount:
        hwm = nav
    return replace(
        snapshot, nav=nav, high_water_mark=hwm, uncertainty_reasons=tuple(sorted(set(reasons)))
    )
