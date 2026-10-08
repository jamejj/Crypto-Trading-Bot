"""Catch optimistic liquidation marks, repeated costs and funding-induced HWM resets."""

import importlib
from dataclasses import replace
from decimal import localcontext

import pytest
from accounting_helpers import BTC, NOW, D, balance, fill, funded, funding, reservation

from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import CostEstimate, MarketSnapshot


def market(price="10", quantity="100"):
    return MarketSnapshot(
        BTC,
        NOW,
        NOW,
        Money(D("999"), "Q"),
        ((Money(D(price), "Q"), Quantity(D(quantity), "BTC")),),
        (),
        1,
        (),
    )


def cost(amount="0"):
    return CostEstimate(
        (),
        D("0.99"),
        Money(D(amount), "Q"),
        "synthetic remaining sale fee; bid already includes spread",
        (),
        basis="REMAINING_AFTER_BIDS",
    )


def value(book, price="10", quantity="100", expense="0"):
    try:
        importlib.import_module("trading_bot.accounting.nav")
    except ModuleNotFoundError:
        pytest.fail("missing conservative valuation behavior")
    return book.value(book.snapshot, (market(price, quantity),), (cost(expense),))


def test_deposit_preserves_unit_nav_and_hwm():
    book = funded()
    book.post_fill(fill())
    state = value(book, "5")
    assert state.nav.amount == D("0.9")
    assert state.high_water_mark.amount == D("1")
    state = funding(book, "deposit", Money(D("9"), "Q"))
    assert state.units == D("110")
    assert state.nav.amount == D("0.9")
    assert state.high_water_mark.amount == D("1")
    state = value(book, "5")
    assert state.nav.amount == D("0.9")


def test_reserved_cash_not_double_counted():
    book = funded()
    book.reserve(reservation(book), book.snapshot.version)
    assert value(book).nav.amount == D("1")


def test_unpriced_asset_marks_nav_uncertain_without_hwm_raise():
    book = funded()
    book.post_fill(fill())
    value(book)
    state = book.value(book.snapshot, (), ())
    assert state.nav.amount == D("0.8")
    assert "UNPRICED:BTC" in state.uncertainty_reasons
    assert state.high_water_mark.amount == D("1")


def test_shallow_book_uses_available_bids_only_and_marks_uncertainty():
    book = funded()
    book.post_fill(fill())
    state = value(book, "10", "0.5")
    assert state.nav.amount == D("0.85")
    assert "INSUFFICIENT_DEPTH:BTC" in state.uncertainty_reasons


def test_bid_spread_is_not_deducted_again_and_actual_fee_is_not_sale_cost():
    from accounting_helpers import fee

    book = funded()
    book.post_fill(fill(fees=(fee("1", "Q"),)))
    state = value(book, "9", expense="0.5")
    assert state.nav.amount == D("0.965")  # (79 + 18 - 0.5) / 100


def test_dust_remains_in_inventory():
    book = funded()
    book.post_fill(fill())
    state = book.mark_dust(BTC, Quantity(D("3"), "BTC"))
    assert state.inventory[0].dust is True
    assert balance(state, "BTC") == D("2")
    assert value(book).nav.amount == D("1")


def test_cashflow_with_uncertain_nav_rejected():
    book = funded()
    book.post_fill(fill())
    with pytest.raises(ValueError, match="certain"):
        funding(book, "deposit", Money(D("9"), "Q"))


def test_low_decimal_context_does_not_round_fill_cash():
    book = funded()
    with localcontext() as ctx:
        ctx.prec = 3
        state = book.post_fill(fill(qty="1.23456789", price="12.3456789"))
    assert balance(state, "Q") == D("84.758421249809479")


def test_stale_valuation_snapshot_rejected():
    book = funded()
    old = book.snapshot
    book.reserve(reservation(book), book.snapshot.version)
    with pytest.raises(ValueError, match="version"):
        book.value(old, (market(),), (cost(),))


def test_full_withdrawal_and_redeposit_preserves_epoch_nav_and_hwm():
    book = funded()
    book.post_fill(fill())
    book.post_fill(replace(fill("sell", "SELL", price="5"), order_id="sell"))
    value(book)
    funding(book, "withdraw", Money(D("-90"), "Q"))
    state = funding(book, "redeposit", Money(D("90"), "Q"))
    assert state.nav.amount == D("0.9")
    assert state.high_water_mark.amount == D("1")
    assert state.units == D("100")


def test_trigger_relative_cost_cannot_double_count_executable_bid_spread():
    book = funded()
    book.post_fill(fill())
    with pytest.raises(ValueError, match="already contained"):
        book.value(book.snapshot, (market(),), (replace(cost(), basis="TRIGGER_SHORTFALL"),))


def test_valuation_of_empty_funded_epoch_retains_unit_nav_for_redeposit():
    book = funded()
    funding(book, "withdraw", Money(D("-100"), "Q"))
    state = value(book)
    assert state.nav.amount == D("1")
    assert funding(book, "redeposit", Money(D("100"), "Q")).units == D("100")


def test_final_zero_fee_requires_revaluation_before_external_funding():
    book = funded()
    book.reserve(reservation(book, fees=(Money(D("4"), "Q"),)), book.snapshot.version)
    book.post_fill(replace(fill(), fee_final=False))
    assert value(book).nav.amount == D(".98")
    state = book.post_fill(fill())
    assert state.uncertainty_reasons == ("VALUATION_REQUIRED",)
    with pytest.raises(ValueError, match="certain"):
        funding(book, "deposit", Money(D("10"), "Q"))
