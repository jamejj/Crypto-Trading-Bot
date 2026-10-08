"""Generated legal executions catch imbalance, non-idempotence and lossy replay."""

from dataclasses import replace
from decimal import Decimal as D

# Independent fixtures; generated expected balances use elementary integer quantities.
from accounting_helpers import balance, fee, fill, funding
from hypothesis import given, settings
from hypothesis import strategies as st

from trading_bot.accounting.ledger import Ledger
from trading_bot.domain.money import Money


@settings(deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(1, 5), st.integers(1, 20), st.integers(0, 10)),
        min_size=1,
        max_size=15,
    )
)
def test_double_entry_and_replay_for_generated_fill_fee_exit_sequences(sequence):
    book = Ledger("a", "Q")
    funding(book, "fund", Money(D("10000"), "Q"))
    expected_cash = D("10000")
    for index, (quantity, price, fee_tenths) in enumerate(sequence):
        trade = f"buy:{index}"
        charge = D(fee_tenths) / D(10)
        buy = replace(
            fill(
                trade,
                qty=str(quantity),
                price=str(price),
                fees=(fee(str(charge), "Q", trade_id=trade),),
            ),
            order_id=trade,
        )
        book.post_fill(buy)
        before = book.snapshot
        assert book.post_fill(replace(buy, source_id="rest")) == before
        sell = replace(
            fill(f"sell:{index}", "SELL", str(quantity), str(price)), order_id=f"sell:{index}"
        )
        book.post_fill(sell)
        expected_cash -= charge
        assert balance(book.snapshot, "Q") == expected_cash
        assert balance(book.snapshot, "BTC") == D("0")
    for currency in {posting.value.currency for posting in book.entries}:
        assert sum(p.value.amount for p in book.entries if p.value.currency == currency) == D("0")
    restored = Ledger.replay("a", "Q", book.operations)
    assert restored.snapshot == book.snapshot
    assert restored.entries == book.entries


@settings(max_examples=40, deadline=None, derandomize=True)
@given(
    st.lists(
        st.tuples(st.sampled_from(["Q", "BTC", "TOKEN"]), st.booleans(), st.integers(1, 3)),
        min_size=1,
        max_size=8,
    )
)
def test_legal_reservations_partial_fills_late_fees_dust_and_funding_replay(sequence):
    from accounting_helpers import BTC, NOW, reservation

    from trading_bot.accounting.reservations import commitments
    from trading_bot.domain.money import Quantity
    from trading_bot.domain.records import (
        CostEstimate,
        InstrumentId,
        MarketSnapshot,
        OrderObservation,
    )

    book = Ledger("a", "Q")
    funding(book, "fund", Money(D("10000"), "Q"))
    token = InstrumentId("synthetic", "TOKEN_Q", "TOKEN", "Q")
    book.post_fill(
        replace(
            fill("token-seed", qty="20", price="1"),
            instrument=token,
            quantity=Quantity(D("20"), "TOKEN"),
            order_id="token-seed",
        )
    )
    book.post_fill(replace(fill("base-seed", qty="10"), order_id="base-seed"))

    def check(previous_version):
        assert book.version >= previous_version
        for currency in {entry.value.currency for entry in book.entries}:
            assert sum(
                entry.value.amount for entry in book.entries if entry.value.currency == currency
            ) == D("0")
            owned = balance(book.snapshot, currency)
            encumbered = commitments(book.snapshot.reservations, currency)
            liabilities = sum(
                m.amount for m in book.snapshot.fee_commitments if m.currency == currency
            )
            assert owned >= encumbered + liabilities
        for currency in ["BTC", "TOKEN"]:
            assert balance(book.snapshot, currency) == sum(
                lot.quantity.amount
                for lot in book.snapshot.inventory
                if lot.instrument.base == currency
            )
        restored = Ledger.replay("a", "Q", book.operations)
        assert restored.snapshot == book.snapshot
        assert restored.entries == book.entries

    def valuation():
        markets = tuple(
            MarketSnapshot(
                instrument,
                NOW,
                NOW,
                Money(price, "Q"),
                ((Money(price, "Q"), Quantity(D("10000"), instrument.base)),),
                (),
                1,
                (),
            )
            for instrument, price in [(BTC, D("9")), (token, D("1"))]
        )
        costs = tuple(
            CostEstimate(
                (), D(".99"), Money(D("0"), "Q"), "synthetic", (), basis="REMAINING_AFTER_BIDS"
            )
            for _ in markets
        )
        book.value(book.snapshot, markets, costs)

    for index, (currency, unknown, charge_units) in enumerate(sequence):
        order, trade = f"order:{index}", f"trade:{index}"
        buffer = D("4") if currency == "Q" else D(".4")
        charge = D(charge_units) / (D("10") if currency == "Q" else D("100"))
        req = replace(
            reservation(book, fees=(Money(buffer, currency),)),
            reservation_id=f"r:{index}",
            order_id=order,
        )
        previous = book.version
        book.reserve(req, previous)
        check(previous)
        actual = fee(str(charge), currency, trade_id=trade)
        observation = replace(
            fill(trade, fees=() if unknown else (actual,)), order_id=order, fee_final=not unknown
        )
        previous = book.version
        book.post_fill(observation)
        check(previous)
        previous = book.version
        if unknown:
            book.post_fee_correction(actual)
            book.post_fill(replace(observation, fees=(actual,), fee_final=True))
        book.post_fee_correction(
            replace(
                actual,
                revision=2,
                value=Money(charge + (D(".1") if currency == "Q" else D(".01")), currency),
            )
        )
        check(previous)
        previous = book.version
        book.release(
            req.reservation_id,
            OrderObservation(
                "terminal", "a", BTC, order, Quantity(D("2"), "BTC"), "CANCELED", True, NOW
            ),
        )
        check(previous)
        previous = book.version
        book.post_fill(replace(fill(f"sell:{index}", "SELL", "1", "9"), order_id=f"sell:{index}"))
        book.mark_dust(BTC, Quantity(D("100"), "BTC"))
        valuation()
        check(previous)
        previous = book.version
        funding(book, f"flow:{index}", Money(D("10"), "Q"))
        check(previous)
