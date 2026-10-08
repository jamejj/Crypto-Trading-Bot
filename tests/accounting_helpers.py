"""Catch duplicate economics, incorrect fees, and spending encumbered assets."""

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import Fee, Fill, InstrumentId, Reservation

NOW = datetime(2026, 1, 1, tzinfo=UTC)
BTC = InstrumentId("synthetic", "BTC_Q", "BTC", "Q")


def ledger():
    try:
        module = importlib.import_module("trading_bot.accounting.ledger")
    except ModuleNotFoundError:
        pytest.fail("missing ledger accounting behavior")
    return module.Ledger("a", "Q")


def fill(trade_id="t1", side="BUY", qty="2", price="10", fees=(), **changes):
    return Fill(
        "ws:1",
        "a",
        BTC,
        "o1",
        side,
        Quantity(D(qty), "BTC"),
        Money(D(price), "Q"),
        fees,
        NOW,
        trade_id=trade_id,
        fee_final=True,
        **changes,
    )


def fee(amount="0.1", currency="BTC", revision=1, trade_id="t1"):
    return Fee("fee:1", "a", BTC, trade_id, Money(D(amount), currency), revision)


def funded():
    book = ledger()
    funding(book, "fund", Money(D("100"), "Q"))
    return book


def balance(snapshot, currency):
    return next((m.amount for m in snapshot.balances if m.currency == currency), D("0"))


def reservation(book, amount="40", qty="4", fees=()):
    return Reservation(
        "r1",
        "i1",
        Money(D(amount), "Q"),
        Money(D("4"), "Q"),
        book.snapshot.version,
        NOW + timedelta(minutes=1),
        "PENDING",
        account_id="a",
        order_id="o1",
        instrument=BTC,
        side="BUY",
        quantity=Quantity(D(qty), "BTC"),
        fee_buffers=fees,
    )


def funding(book, source_id, value, **kwargs):
    from trading_bot.domain.records import FundingEvidence

    return book.post_cashflow(
        source_id,
        value,
        evidence=FundingEvidence("offline-fixture", "a", book.snapshot.version, True, True, NOW),
        **kwargs,
    )
