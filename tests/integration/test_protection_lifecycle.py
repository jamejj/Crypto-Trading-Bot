from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from accounting_helpers import BTC, NOW, D, fill
from execution_helpers import execution_module, execution_repo, prepare  # noqa: F401

from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import ExitRequest, OrderObservation
from trading_bot.operations.clock import SimulationClock


@pytest.fixture
def lifecycle_repo(execution_repo):  # noqa: F811
    repo, connection = execution_repo
    module = execution_module("storage.lifecycle_repository")
    with connection() as conn:
        conn.execute(Path("migrations/002b_protection_exits.sql").read_text())
    p = execution_module("execution.protection")
    lifecycle = module.LifecycleRepository(repo)
    clock = p.ProtectionClock(SimulationClock(NOW))
    lifecycle.configure(p.ProtectionContext(BTC, Money(D("9"), "Q"), D("1")))
    return lifecycle, repo, connection, clock


def test_fill_stop_command_reservation_and_deadline_restart_together(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    first = lifecycle.get(BTC)
    assert first.context.stop_status == "PENDING"
    assert first.context.protection.covered.amount == 0
    clock.clock.advance(D("4"))
    lifecycle.apply_entry_event(intent.intent_id, fill("t2"), clock)
    restarted = type(lifecycle)(type(repo)(repo.connect, "a", "Q"))
    state = restarted.get(BTC)
    assert state.context.protection.target.amount == 4
    assert len(restarted.commands(BTC)) == 1
    assert state.context.protection.lot_deadlines[0].deadline == NOW + timedelta(seconds=5)
    overdue_clock = execution_module("execution.protection").ProtectionClock(
        SimulationClock(NOW + timedelta(seconds=6))
    )
    assert restarted.assess(BTC, overdue_clock).context.protection.status == "OVERDUE"
    assert (
        sum(r.quantity.amount for r in repo.ledger.snapshot.reservations if r.side == "SELL") == 2
    )


@pytest.mark.parametrize("autocommit", [False, True])
def test_command_failure_rolls_back_fill_deadline_and_reservation(lifecycle_repo, autocommit):
    lifecycle, repo, connection, clock = lifecycle_repo
    intent = prepare(repo)
    before = repo.ledger.snapshot

    def factory():
        conn = connection()
        conn.autocommit = autocommit
        return conn

    lifecycle = type(lifecycle)(type(repo)(factory, "a", "Q"))
    with connection() as conn:
        conn.execute("""CREATE FUNCTION fail_lifecycle_command() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'command failed'; END $$""")
        conn.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON lifecycle_commands "
            "FOR EACH ROW EXECUTE FUNCTION fail_lifecycle_command()"
        )
    with pytest.raises(psycopg.Error, match="command failed"):
        lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    assert repo.ledger.snapshot == before
    assert lifecycle.get(BTC).context.protection is None
    assert repo.get_state(intent.intent_id).filled is None


def stop_observation(state, status="ACTIVE", qty="0", terminal=False):
    return OrderObservation(
        "stop:" + status + qty,
        "a",
        BTC,
        state.context.stop_id,
        Quantity(D(qty), "BTC"),
        status,
        terminal,
        NOW,
    )


def verify(lifecycle, policy):
    e = execution_module("execution.exits")
    lifecycle.select_policy(
        BTC, policy, e.SyntheticExitVerification("a", BTC, policy, "synthetic:fixture", True)
    )


def request():
    return ExitRequest(
        "exit1", BTC, Quantity(D("4"), "BTC"), "time", "a", NOW + timedelta(seconds=5)
    )


def confirmed(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(qty="4"), clock)
    state = lifecycle.get(BTC)
    lifecycle.observe_stop(
        stop_observation(state),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("4"), "BTC"),
    )
    return lifecycle, repo, clock


def test_serial_cancel_ack_stop_fill_then_reconciliation_sells_only_remaining(lifecycle_repo):
    lifecycle, repo, clock = confirmed(lifecycle_repo)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    cancel = lifecycle.request_exit(request(), clock)[0]
    assert cancel.kind == "CANCEL_STOP"
    assert lifecycle.get(BTC).serial_deadline == NOW + timedelta(seconds=5)
    state = lifecycle.get(BTC)
    lifecycle.observe_stop(stop_observation(state, "CANCEL_PENDING"), clock)
    assert lifecycle.request_exit(request(), clock) == []
    lifecycle.apply_stop_fill(
        replace(fill("stopfill", side="SELL", qty="1"), order_id=state.context.stop_id), clock
    )
    lifecycle.observe_stop(stop_observation(state, "CANCELED", "1", True), clock)
    sell = lifecycle.request_exit(request(), clock)[0]
    assert (sell.kind, sell.quantity.amount) == ("MARKET_SELL", 3)
    assert lifecycle.request_exit(request(), clock) == []
    assert (
        sum(
            r.quantity.amount
            for r in repo.ledger.snapshot.reservations
            if r.side == "SELL" and r.status == "PENDING"
        )
        == 3
    )


def test_terminal_cancel_missing_trade_cannot_release_or_sell(lifecycle_repo):
    lifecycle, repo, clock = confirmed(lifecycle_repo)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    lifecycle.request_exit(request(), clock)
    lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), "CANCELED", "1", True), clock)
    assert lifecycle.request_exit(request(), clock) == []
    assert (
        sum(
            r.quantity.amount
            for r in repo.ledger.snapshot.reservations
            if r.side == "SELL" and r.status == "PENDING"
        )
        == 4
    )


@pytest.mark.parametrize("change", ["side", "trigger", "quantity"])
def test_wrong_stop_parameters_never_confirm_coverage(lifecycle_repo, change):
    lifecycle, repo, _, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(), clock)
    args = {"side": "SELL", "trigger": Money(D("9"), "Q"), "quantity": Quantity(D("2"), "BTC")}
    args[change] = {
        "side": "BUY",
        "trigger": Money(D("8"), "Q"),
        "quantity": Quantity(D("3"), "BTC"),
    }[change]
    with pytest.raises(ValueError):
        lifecycle.observe_stop(stop_observation(lifecycle.get(BTC)), clock, **args)
    assert lifecycle.get(BTC).context.protection.covered.amount == 0


def test_native_linked_fake_atomic_exit_retires_stop_without_second_reservation(lifecycle_repo):
    lifecycle, repo, clock = confirmed(lifecycle_repo)
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    command = lifecycle.request_exit(request(), clock)[0]
    assert command.kind == "NATIVE_LINKED_EXIT"
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange()
    fake.execute(lifecycle, command.command_id, clock)
    assert lifecycle.get(BTC).sell_status == "FILLED"
    assert lifecycle.get(BTC).context.protection.target.amount == 0
    assert sum(lot.quantity.amount for lot in repo.ledger.snapshot.inventory) == 0
    assert fake.execute(lifecycle, command.command_id, clock) is False


def test_lifecycle_audit_and_commands_are_immutable(lifecycle_repo):
    lifecycle, repo, connection, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(), clock)
    for table in ("lifecycle_audit", "lifecycle_commands"):
        with pytest.raises(psycopg.Error, match="immutable"):
            with connection() as conn:
                conn.execute("DELETE FROM " + table)


def test_serial_fake_market_sell_and_restart_never_duplicate(lifecycle_repo):
    lifecycle, repo, clock = confirmed(lifecycle_repo)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange()
    cancel = lifecycle.request_exit(request(), clock)[0]
    assert fake.execute(lifecycle, cancel.command_id, clock)
    assert lifecycle.request_exit(request(), clock) == []
    lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), "CANCELED", "0", True), clock)
    sell = lifecycle.request_exit(request(), clock)[0]
    assert fake.execute(lifecycle, sell.command_id, clock)
    restarted = type(lifecycle)(type(repo)(repo.connect, "a", "Q"))
    assert restarted.get(BTC).sell_status == "FILLED"
    assert fake.execute(restarted, sell.command_id, clock) is False
    assert sum(lot.quantity.amount for lot in repo.ledger.snapshot.inventory) == 0


def test_fake_storage_loss_leaves_unknown_sell_and_no_retry(lifecycle_repo):
    lifecycle, repo, connection, clock = lifecycle_repo
    lifecycle, repo, clock = confirmed(lifecycle_repo)
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    proposal = lifecycle.request_exit(request(), clock)[0]
    before = repo.ledger.snapshot
    with connection() as conn:
        conn.execute("""CREATE FUNCTION fail_effect() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF (SELECT count(*) FROM ledger_operations WHERE method='_fill') > 1 THEN
          RAISE EXCEPTION 'effect persistence failed';
        END IF;
        RETURN NEW; END $$""")
        conn.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON lifecycle_audit "
            "FOR EACH ROW EXECUTE FUNCTION fail_effect()"
        )
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange()
    with pytest.raises(psycopg.Error, match="effect persistence failed"):
        fake.execute(lifecycle, proposal.command_id, clock)
    assert repo.ledger.snapshot == before
    assert fake.execute(lifecycle, proposal.command_id, clock) is False
    assert lifecycle.get(BTC).sell_status == "UNKNOWN"
    assert lifecycle.get(BTC).context.protection.covered.amount == 0
    with connection() as conn:
        conn.execute("DROP TRIGGER fail ON lifecycle_audit")
    assert lifecycle.request_exit(request(), clock) == []


def test_stop_duplicate_identity_and_terminal_contradictions_rejected(lifecycle_repo):
    lifecycle, _, clock = confirmed(lifecycle_repo)
    active = stop_observation(lifecycle.get(BTC))
    with pytest.raises(ValueError, match="identity"):
        lifecycle.observe_stop(replace(active, status="UNKNOWN"), clock)
    terminal = stop_observation(lifecycle.get(BTC), "CANCELED", "0", True)
    lifecycle.observe_stop(terminal, clock)
    with pytest.raises(ValueError, match="terminal"):
        lifecycle.observe_stop(
            replace(
                terminal,
                source_id="conflict",
                status="FILLED",
                observed_at=NOW + timedelta(seconds=1),
            ),
            clock,
        )
    with pytest.raises(ValueError, match="terminal"):
        lifecycle.observe_stop(replace(terminal, source_id="badterminal", terminal=False), clock)


def test_stale_native_command_after_stop_fill_does_not_oversell(lifecycle_repo):
    lifecycle, repo, clock = confirmed(lifecycle_repo)
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    proposal = lifecycle.request_exit(request(), clock)[0]
    lifecycle.apply_stop_fill(
        replace(fill("stoprace", side="SELL", qty="1"), order_id=proposal.stop_id), clock
    )
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange()
    with pytest.raises(ValueError, match="current net"):
        fake.execute(lifecycle, proposal.command_id, clock)
    assert sum(lot.quantity.amount for lot in repo.ledger.snapshot.inventory) == 3
    assert lifecycle.get(BTC).sell_status == "UNKNOWN"
    assert fake.execute(lifecycle, proposal.command_id, clock) is False


def test_competing_base_reservation_in_other_market_blocks_duplicate_sell_right(lifecycle_repo):
    from accounting_helpers import reservation

    from trading_bot.domain.records import InstrumentId

    lifecycle, repo, _, clock = lifecycle_repo
    repo.apply_event(prepare(repo).intent_id, fill())
    other = InstrumentId("synthetic", "BTC_ALT_Q", "BTC", "Q")
    item = replace(
        reservation(repo.ledger),
        reservation_id="other-right",
        order_id="other-stop",
        intent_id="other",
        instrument=other,
        side="SELL",
        quantity=Quantity(D("2"), "BTC"),
        cash=Money(D("0"), "Q"),
    )
    repo.ledger.reserve(item, item.ledger_version)
    state = lifecycle.assess(BTC, clock)
    assert state.context.protection.status == "CONFLICT"
    assert state.context.protection.uncovered.amount == 2
    assert lifecycle.commands(BTC) == []


def test_delayed_fake_cancel_ack_cannot_regress_terminal_or_block_prepared_sell(lifecycle_repo):
    lifecycle, _, clock = confirmed(lifecycle_repo)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    cancel = lifecycle.request_exit(request(), clock)[0]
    terminal = stop_observation(lifecycle.get(BTC), "CANCELED", "0", True)
    lifecycle.observe_stop(terminal, clock)
    sell = lifecycle.request_exit(request(), clock)[0]
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange()
    fake.execute(lifecycle, cancel.command_id, clock)
    assert lifecycle.get(BTC).stop_observation == terminal
    assert fake.execute(lifecycle, sell.command_id, clock)


@pytest.mark.parametrize("status,terminal", [("CANCELED", True), ("ACTIVE", False)])
def test_observed_stop_never_replays_original_fake_create(lifecycle_repo, status, terminal):
    lifecycle, repo, _, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(), clock)
    create = lifecycle.commands(BTC)[0]
    terminal = stop_observation(lifecycle.get(BTC), "CANCELED", "0", True)
    lifecycle.observe_stop(terminal, clock)
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange()
    assert fake.execute(lifecycle, create.command_id, clock) is False
    assert lifecycle.get(BTC).context.stop_status == "CANCELED"


def test_active_stop_with_missing_trade_is_unknown_not_coverage(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(), clock)
    state = lifecycle.get(BTC)
    lifecycle.observe_stop(
        stop_observation(state, "ACTIVE", "1"),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    assert lifecycle.get(BTC).context.protection.covered.amount == 0
    assert lifecycle.get(BTC).context.stop_status == "UNKNOWN"
