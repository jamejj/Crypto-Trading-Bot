"""Catch duplicate economics, incorrect fees, and spending encumbered assets."""

from dataclasses import replace

import pytest
from accounting_helpers import BTC, NOW, D, balance, fee, fill, funded, funding, ledger, reservation

from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import OrderObservation


def test_same_fill_from_ws_and_rest_posts_once():
    book = funded()
    first = book.post_fill(fill())
    again = book.post_fill(replace(fill(), source_id="rest:9"))
    assert again == first
    assert balance(again, "Q") == D("80")
    assert balance(again, "BTC") == D("2")


def test_conflicting_same_trade_observation_rejected_without_mutation():
    book = funded()
    book.post_fill(fill())
    before = book.snapshot
    with pytest.raises(ValueError, match="conflict"):
        book.post_fill(fill(price="11"))
    assert book.snapshot == before


def test_fill_fee_in_base_changes_sellable_quantity():
    book = funded()
    state = book.post_fill(fill(fees=(fee(),)))
    assert balance(state, "BTC") == D("1.9")
    assert state.inventory[0].quantity.amount == D("1.9")
    assert state.inventory[0].fees == (fee(),)


def test_fee_revision_posts_delta_not_second_fill():
    book = funded()
    book.post_fill(fill(fees=(fee("1", "Q"),)))
    state = book.post_fee_correction(fee("1.5", "Q", 2))
    assert balance(state, "Q") == D("78.5")
    assert balance(state, "BTC") == D("2")
    assert book.post_fee_correction(fee("1.5", "Q", 2)) == state
    with pytest.raises(ValueError, match="conflict"):
        book.post_fee_correction(fee("2", "Q", 2))


def test_other_currency_fee_debits_actual_asset():
    book = funded()
    from trading_bot.domain.records import InstrumentId

    token = InstrumentId("synthetic", "TOKEN_Q", "TOKEN", "Q")
    book.post_fill(
        replace(
            fill("token-buy", qty="3", price="1"),
            instrument=token,
            quantity=Quantity(D("3"), "TOKEN"),
            order_id="token-order",
        )
    )
    state = book.post_fill(fill(fees=(fee("0.5", "TOKEN"),)))
    assert balance(state, "TOKEN") == D("2.5")
    assert balance(state, "Q") == D("77")


def test_reservations_prevent_double_spending_cash_and_fee_currency():
    book = funded()
    book.reserve(reservation(book, "80", fees=(Money(D("10"), "Q"),)), book.snapshot.version)
    before = book.snapshot
    with pytest.raises(ValueError, match="available"):
        book.reserve(
            replace(reservation(book, "11"), reservation_id="r2", order_id="o2"),
            book.snapshot.version,
        )
    assert book.snapshot == before


def test_stale_reservation_version_rejected():
    book = funded()
    with pytest.raises(ValueError, match="version"):
        book.reserve(reservation(book), book.snapshot.version - 1)


def test_partial_fill_consumes_matching_reservation_and_transfers_risk():
    book = funded()
    book.reserve(reservation(book, fees=(Money(D("2"), "Q"),)), book.snapshot.version)
    state = book.post_fill(fill(fees=(fee("1", "Q"),)))
    pending = state.reservations[0]
    assert pending.cash.amount == D("20")
    assert pending.quantity.amount == D("2")
    assert pending.risk.amount == D("2")
    assert pending.fee_buffers == (Money(D("1"), "Q"),)
    assert state.inventory[0].risk.amount == D("2")


def test_release_requires_terminal_evidence_with_all_fills_reconciled():
    book = funded()
    book.reserve(reservation(book), book.snapshot.version)
    for status, terminal, cumulative in [
        ("CANCEL_ACK", False, "0"),
        ("TIMEOUT", False, "0"),
        ("CANCELED", True, "2"),
    ]:
        before = book.snapshot
        observation = OrderObservation(
            "obs", "a", BTC, "o1", Quantity(D(cumulative), "BTC"), status, terminal, NOW
        )
        with pytest.raises(ValueError, match="terminal|reconcile"):
            book.release("r1", observation)
        assert book.snapshot == before
    state = book.release(
        "r1",
        OrderObservation("obs", "a", BTC, "o1", Quantity(D("0"), "BTC"), "CANCELED", True, NOW),
    )
    assert state.reservations[0].status == "RELEASED"


def test_sell_cannot_spend_base_reserved_for_another_order():
    book = funded()
    book.post_fill(fill())
    sell = replace(
        reservation(book, "0", "2"), reservation_id="sell-r", order_id="sell-o", side="SELL"
    )
    book.reserve(sell, book.snapshot.version)
    with pytest.raises(ValueError, match="available"):
        book.post_fill(replace(fill("t2", "SELL", "1"), order_id="other"))


def test_failed_fee_does_not_leave_partially_posted_fill():
    book = funded()
    before = book.snapshot
    with pytest.raises(ValueError, match="available"):
        book.post_fill(fill(fees=(fee("1", "TOKEN"),)))
    assert book.snapshot == before
    assert len(book.entries) == 2


def test_unknown_fee_survives_terminal_release_then_actual_fee_replaces_commitment():
    book = funded()
    book.reserve(reservation(book, fees=(Money(D("2"), "Q"),)), book.snapshot.version)
    book.post_fill(replace(fill(), fee_final=False))
    state = book.release(
        "r1",
        OrderObservation("obs", "a", BTC, "o1", Quantity(D("2"), "BTC"), "CANCELED", True, NOW),
    )
    assert state.fee_commitments == (Money(D("1"), "Q"),)
    state = book.post_fee_correction(fee("0.5", "Q"))
    assert state.fee_commitments == ()
    assert balance(state, "Q") == D("79.5")


def test_unknown_fee_without_bounded_reservation_rejected():
    book = funded()
    with pytest.raises(ValueError, match="unknown fee"):
        book.post_fill(replace(fill(), fee_final=False))
    assert balance(book.snapshot, "Q") == D("100")


def test_late_base_fee_cannot_reassign_another_lots_quantity():
    book = funded()
    book.post_fill(fill())
    book.post_fill(replace(fill("t2", "SELL"), order_id="sell"))
    book.post_fill(replace(fill("t3"), order_id="new-buy"))
    before = book.snapshot
    with pytest.raises(ValueError, match="lot"):
        book.post_fee_correction(fee())
    assert book.snapshot == before


def test_sell_base_fee_reduces_remaining_inventory():
    book = funded()
    book.post_fill(fill())
    state = book.post_fill(
        replace(
            fill("sell", "SELL", "1", fees=(fee("0.1", "BTC", trade_id="sell"),)), order_id="sell-o"
        )
    )
    assert balance(state, "BTC") == D("0.9")
    assert state.inventory[0].quantity.amount == D("0.9")


def test_fee_cannot_spend_surviving_principal_reservation():
    book = funded()
    book.reserve(reservation(book, "98", "2", (Money(D("2"), "Q"),)), book.snapshot.version)
    before = book.snapshot
    with pytest.raises(ValueError, match="available"):
        book.post_fill(fill(qty="1", price="49", fees=(fee("3", "Q"),)))
    assert book.snapshot == before


def test_late_quote_fee_allocates_only_remaining_acquisition_share():
    book = funded()
    book.post_fill(fill())
    book.post_fill(replace(fill("sell", "SELL", "1"), order_id="sell-o"))
    state = book.post_fee_correction(fee("2", "Q"))
    assert state.inventory[0].cost.amount == D("11")


def test_base_fee_rebate_after_full_exit_returns_explicit_inventory():
    book = funded()
    book.post_fill(fill(fees=(fee("0.2"),)))
    book.post_fill(replace(fill("sell", "SELL", "1.8"), order_id="sell-o"))
    state = book.post_fee_correction(fee("0.1", revision=2))
    assert balance(state, "BTC") == D("0.1")
    assert sum(lot.quantity.amount for lot in state.inventory) == D("0.1")


def test_multiple_unknown_fees_settle_only_matching_trade_component():
    book = funded()
    book.reserve(reservation(book, fees=(Money(D("4"), "Q"),)), book.snapshot.version)
    book.post_fill(replace(fill(qty="1"), fee_final=False))
    book.post_fill(replace(fill("t2", qty="1"), fee_final=False))
    assert book.snapshot.fee_commitments == (Money(D("2"), "Q"),)
    state = book.post_fee_correction(fee("0.5", "Q"))
    assert state.fee_commitments == (Money(D("1"), "Q"),)


def test_late_unknown_fee_settlement_preserves_other_pending_fee_buffer():
    book = funded()
    book.reserve(reservation(book, fees=(Money(D("4"), "Q"),)), book.snapshot.version)
    book.post_fill(replace(fill(), fee_final=False))
    state = book.post_fee_correction(fee("1", "Q"))
    assert state.reservations[0].fee_buffers == (Money(D("2"), "Q"),)


def test_final_zero_fee_observation_releases_only_same_trade_unknown_commitment():
    book = funded()
    book.reserve(reservation(book, fees=(Money(D("4"), "Q"),)), book.snapshot.version)
    book.post_fill(replace(fill(), fee_final=False))
    state = book.post_fill(fill())
    assert state.fee_commitments == ()
    assert state.reservations[0].fee_buffers == (Money(D("2"), "Q"),)


def test_third_currency_fee_consumes_its_acquired_inventory():
    from trading_bot.domain.money import Quantity
    from trading_bot.domain.records import InstrumentId

    book = funded()
    token = InstrumentId("synthetic", "TOKEN_Q", "TOKEN", "Q")
    book.post_fill(
        replace(
            fill("token-buy", qty="3", price="1"),
            instrument=token,
            quantity=Quantity(D("3"), "TOKEN"),
            order_id="token-order",
        )
    )
    state = book.post_fill(fill(fees=(fee("0.5", "TOKEN"),)))
    assert balance(state, "TOKEN") == D("2.5")
    assert next(lot for lot in state.inventory if lot.instrument == token).quantity.amount == D(
        "2.5"
    )


def test_external_funding_requires_pause_and_reconciliation_evidence():
    book = ledger()
    with pytest.raises(ValueError, match="evidence"):
        book.post_cashflow("fund", Money(D("100"), "Q"))


def test_terminal_evidence_cannot_mix_quantity_asset_units():
    book = funded()
    book.reserve(reservation(book), book.snapshot.version)
    before = book.snapshot
    with pytest.raises(ValueError, match="terminal|quantity"):
        book.release(
            "r1",
            OrderObservation("obs", "a", BTC, "o1", Quantity(D("0"), "ETH"), "CANCELED", True, NOW),
        )
    assert book.snapshot == before


def test_nonquote_external_funding_rejected_in_v1():
    book = funded()
    with pytest.raises(ValueError, match="quote currency"):
        funding(book, "token", Money(D("3"), "TOKEN"), quote_value=Money(D("3"), "Q"))


def test_known_fee_revision_preserves_unfilled_fee_buffer():
    book = funded()
    book.reserve(reservation(book, fees=(Money(D("4"), "Q"),)), book.snapshot.version)
    book.post_fill(fill(fees=(fee("2", "Q"),)))
    state = book.post_fee_correction(fee("3", "Q", 2))
    assert state.reservations[0].fee_buffers == (Money(D("2"), "Q"),)


def test_duplicate_fee_buffer_currency_rejected_before_reservation():
    book = funded()
    with pytest.raises(ValueError, match="currency"):
        book.reserve(
            reservation(book, fees=(Money(D("1"), "Q"), Money(D("1"), "Q"))), book.snapshot.version
        )


def test_lot_identity_scopes_trade_id_by_account_and_instrument():
    from trading_bot.domain.records import InstrumentId

    book = funded()
    book.post_fill(fill("1"))
    token = InstrumentId("synthetic", "TOKEN_Q", "TOKEN", "Q")
    state = book.post_fill(
        replace(
            fill("1", qty="3", price="1"),
            instrument=token,
            quantity=Quantity(D("3"), "TOKEN"),
            order_id="token",
        )
    )
    assert len({lot.lot_id for lot in state.inventory}) == 2


def test_unknown_fee_buffer_cannot_claim_zero_as_conservative_bound():
    book = funded()
    with pytest.raises(ValueError, match="positive|buffer"):
        book.reserve(reservation(book, "20", "2", (Money(D("0"), "Q"),)), book.snapshot.version)


def test_embedded_fee_cannot_revise_another_trade():
    book = funded()
    book.post_fill(fill())
    before = book.snapshot
    with pytest.raises(ValueError, match="fee.*identity|fee.*scope"):
        book.post_fill(replace(fill("t2", fees=(fee("1", "Q", trade_id="t1"),)), order_id="o2"))
    assert book.snapshot == before


def test_fifo_exit_preserves_origin_inventory_for_reserved_unknown_base_fee():
    book = funded()
    book.post_fill(replace(fill("seed", qty="1"), order_id="seed"))
    book.reserve(reservation(book, "10", "1", (Money(D(".1"), "BTC"),)), book.snapshot.version)
    book.post_fill(replace(fill(qty="1"), fee_final=False))
    book.post_fill(replace(fill("second", qty="1"), order_id="second"))
    book.post_fill(replace(fill("sell", "SELL", "2"), order_id="sell"))
    state = book.post_fee_correction(fee(".1"))
    assert balance(state, "BTC") == D(".9")
    assert sum(lot.quantity.amount for lot in state.inventory) == D(".9")
    assert state.fee_commitments == ()


def test_rebate_holding_instrument_cannot_collide_with_origin_trade_identity():
    from trading_bot.domain.records import InstrumentId

    book = funded()
    token = InstrumentId("synthetic", "TOKEN_Q", "TOKEN", "Q")
    book.post_fill(
        replace(
            fill("1", qty="3", price="1"),
            instrument=token,
            quantity=Quantity(D("3"), "TOKEN"),
            order_id="token",
        )
    )
    book.post_fill(replace(fill("1", fees=(fee("1", "TOKEN", trade_id="1"),)), order_id="btc"))
    book.post_fee_correction(fee(".5", "TOKEN", 2, trade_id="1"))
    token_fee = replace(fee(".1", "TOKEN", trade_id="1"), instrument=token)
    state = book.post_fee_correction(token_fee)
    assert balance(state, "TOKEN") == D("2.4")
    assert sum(
        lot.quantity.amount for lot in state.inventory if lot.instrument.base == "TOKEN"
    ) == D("2.4")
    rebate = next(
        lot for lot in state.inventory if lot.cost.amount == D("0") and lot.instrument == token
    )
    assert rebate.origin_instrument == BTC


def test_reducer_rejects_inventory_balance_disagreement_before_publishing_operation():
    book = funded()
    book.post_fill(fill())
    book.lots = []  # Deliberate corruption of an existing local projection.
    before = book.snapshot
    with pytest.raises(ValueError, match="ownership"):
        book.reserve(reservation(book), book.snapshot.version)
    assert book.snapshot == before
