from dataclasses import replace
from datetime import timedelta

import pytest
from accounting_helpers import NOW, D, balance, fill, funded, reservation
from execution_helpers import execution_module, observation


def reducer():
    module = execution_module("execution.reducer")
    book = funded()
    book.reserve(reservation(book), book.version)
    state = module.OrderState(
        "i1", "a", reservation(book).instrument, "o1", "BUY", reservation(book).quantity
    )
    return module, state, book


def test_fill_before_ack_and_repeated_channels_post_once():
    module, state, book = reducer()
    state = module.apply(state, fill(), book)
    assert state.status == "PARTIALLY_FILLED"
    state = module.apply(state, observation(), book)
    state = module.apply(state, replace(fill(), source_id="rest"), book)
    assert state.status == "PARTIALLY_FILLED"
    assert balance(book.snapshot, "BTC") == D("2")
    assert balance(book.snapshot, "Q") == D("80")
    assert state.filled.amount == D("2")


def test_cancel_ack_does_not_release_reserve_and_late_fill_survives():
    module, state, book = reducer()
    state = module.apply(state, observation("CANCEL_PENDING"), book)
    state = module.apply(state, observation("CANCELED", "2", True), book)
    assert book.snapshot.reservations[0].cash.amount == 40
    state = module.apply(state, fill(), book)
    assert state.status == "CANCELED"
    assert state.filled.amount == D("2")
    assert balance(book.snapshot, "BTC") == D("2")
    assert book.snapshot.reservations[0].cash.amount == 20


def test_late_canceled_and_status_regression_cannot_undo_full_fill():
    module, state, book = reducer()
    state = module.apply(state, fill(qty="4"), book)
    for event in [observation("CANCELED", "0", True), observation("ACTIVE"), observation()]:
        state = module.apply(state, event, book)
    assert state.status == "FILLED"
    assert balance(book.snapshot, "BTC") == D("4")


def test_older_terminal_status_does_not_replace_newer_evidence():
    module, state, book = reducer()
    state = module.apply(state, observation("ACTIVE", observed_at=NOW + timedelta(seconds=1)), book)
    state = module.apply(state, observation("CANCELED", terminal=True), book)
    assert state.status == "ACTIVE"
    state = module.apply(
        state, observation("ACKNOWLEDGED", observed_at=NOW + timedelta(seconds=2)), book
    )
    assert state.status == "ACTIVE"


def test_fill_scope_conflict_has_no_economic_effect():
    module, state, book = reducer()
    before = book.snapshot
    with pytest.raises(ValueError, match="scope"):
        module.apply(state, replace(fill(), order_id="foreign"), book)
    assert book.snapshot == before


def test_repeated_observation_is_idempotent_and_conflicting_source_is_rejected():
    module, state, book = reducer()
    event = observation("ACTIVE")
    state = module.apply(state, event, book)
    assert module.apply(state, event, book) == state
    before = book.snapshot
    with pytest.raises(ValueError, match="source"):
        module.apply(state, replace(event, status="CANCELED", terminal=True), book)
    assert book.snapshot == before


@pytest.mark.parametrize(
    "event",
    [fill(qty="5"), observation("CANCELED", "5", True), observation("CANCELED", terminal=False)],
)
def test_invalid_evidence_is_rejected_before_ledger_mutation(event):
    module, state, book = reducer()
    before = book.snapshot
    with pytest.raises(ValueError):
        module.apply(state, event, book)
    assert book.snapshot == before
