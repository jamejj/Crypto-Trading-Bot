from dataclasses import replace
from datetime import timedelta

from accounting_helpers import BTC, NOW, D, fee, fill, funded
from execution_helpers import execution_module, observation

from trading_bot.domain.money import Money, Quantity
from trading_bot.execution.reducer import OrderState, apply
from trading_bot.operations.clock import SimulationClock


def context(**changes):
    p = execution_module("execution.protection")
    return replace(p.ProtectionContext(BTC, Money(D("9"), "Q"), D("1")), **changes)


def test_multi_fill_fok_not_terminal_partial():
    book = funded()
    state = OrderState("i1", "a", BTC, "o1", "BUY", Quantity(D("4"), "BTC"))
    state = apply(state, fill(), book)
    assert state.status == "PARTIALLY_FILLED"
    p = execution_module("execution.protection")
    assert not p.terminal_partial(state)
    state = apply(state, observation("CANCELED", "2", True), book)
    assert p.terminal_partial(state)


def test_new_fill_during_unknown_stop_does_not_duplicate_coverage():
    p = execution_module("execution.protection")
    book = funded()
    book.post_fill(fill())
    clock = p.ProtectionClock(SimulationClock(NOW))
    state = p.assess_protection(book.snapshot, context(), clock)
    ctx = context(
        protection=state,
        stop_status="UNKNOWN",
        stop_quantity=Quantity(D("2"), "BTC"),
        stop_id="stop1",
    )
    book.post_fill(fill("t2"))
    result = p.assess_protection(book.snapshot, ctx, clock)
    assert (result.target.amount, result.covered.amount, result.uncovered.amount) == (4, 0, 4)
    assert p.propose_stop(replace(ctx, protection=result), "a") == []


def test_oldest_uncovered_deadline_not_reset():
    p = execution_module("execution.protection")
    book = funded()
    book.post_fill(fill())
    sim = SimulationClock(NOW)
    clock = p.ProtectionClock(sim)
    first = p.assess_protection(book.snapshot, context(), clock)
    sim.advance(D("4"))
    book.post_fill(fill("t2"))
    second = p.assess_protection(book.snapshot, context(protection=first), clock)
    assert [item.deadline for item in second.lot_deadlines] == [
        NOW + timedelta(seconds=5),
        NOW + timedelta(seconds=9),
    ]
    sim.jump_utc(NOW - timedelta(hours=1))
    sim.advance(D("1"))
    assert p.assess_protection(book.snapshot, context(protection=second), clock).status == "OVERDUE"


def test_base_fee_reduces_protectable_quantity_and_small_partial_stays_uncovered():
    p = execution_module("execution.protection")
    book = funded()
    book.post_fill(fill(qty="1", fees=(fee("0.1"),)))
    state = p.assess_protection(book.snapshot, context(), p.ProtectionClock(SimulationClock(NOW)))
    assert state.target.amount == D("0.9")
    assert state.uncovered.amount == D("0.9")
    assert state.lot_deadlines[0].deadline == NOW + timedelta(seconds=5)
    assert p.propose_stop(context(protection=state), "a") == []


def test_pending_base_fee_commitment_reduces_protectable_quantity():
    p = execution_module("execution.protection")
    book = funded()
    book.post_fill(fill())
    snapshot = replace(book.snapshot, fee_commitments=(Money(D("0.2"), "BTC"),))
    state = p.assess_protection(snapshot, context(), p.ProtectionClock(SimulationClock(NOW)))
    assert state.target.amount == D("1.8")


def test_oversized_confirmed_stop_is_conflict_after_base_fee():
    p = execution_module("execution.protection")
    book = funded()
    book.post_fill(fill(fees=(fee("0.1"),)))
    state = p.assess_protection(
        book.snapshot,
        context(
            stop_status="CONFIRMED",
            stop_id="stop1",
            stop_quantity=Quantity(D("2"), "BTC"),
            confirmed_valid=True,
        ),
        p.ProtectionClock(SimulationClock(NOW)),
    )
    assert state.status == "CONFLICT"
    assert state.covered.amount == 0


def test_late_base_fee_on_confirmed_stop_retains_oldest_deadline_and_conflict():
    p = execution_module("execution.protection")
    book = funded()
    book.post_fill(fill())
    sim = SimulationClock(NOW)
    clock = p.ProtectionClock(sim)
    uncovered = p.assess_protection(book.snapshot, context(), clock)
    pending = context(
        protection=uncovered,
        stop_id="stop1",
        stop_status="PENDING",
        stop_quantity=Quantity(D("2"), "BTC"),
    )
    sim.advance(D("2"))
    book.post_fee_correction(fee("0.1"))
    state = p.assess_protection(
        book.snapshot, replace(pending, stop_status="CONFIRMED", confirmed_valid=True), clock
    )
    assert state.status == "CONFLICT"
    assert state.target.amount == D("1.9")
    assert state.covered.amount == 0
    assert state.lot_deadlines[0].deadline == NOW + timedelta(seconds=5)


def test_nonpositive_trigger_cannot_create_protection():
    import pytest

    p = execution_module("execution.protection")
    with pytest.raises(ValueError):
        p.ProtectionContext(BTC, Money(D("0"), "Q"), D("1"))
