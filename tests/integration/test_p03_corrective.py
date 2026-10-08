from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from accounting_helpers import BTC, NOW, D, fill, reservation
from execution_helpers import observation, prepare, writer
from test_protection_lifecycle import lifecycle_repo, stop_observation, verify  # noqa: F401

from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import RiskApproval
from trading_bot.execution.dispatcher import FakeDispatcher, FakeExchange
from trading_bot.execution.protection import ProtectionClock
from trading_bot.operations.clock import SimulationClock
from trading_bot.storage.lifecycle_repository import LifecycleRepository


def another(repo):
    item = replace(reservation(repo.ledger), reservation_id="r2", intent_id="i2", order_id="o2")
    approval = RiskApproval(
        "a2",
        BTC,
        "BUY",
        Quantity(D("4"), "BTC"),
        Money(D("40"), "Q"),
        item.risk,
        Money(D("10"), "Q"),
        Money(D("9"), "Q"),
        item.ledger_version,
        "synthetic",
        item.expires_at,
        False,
    )
    return repo.prepare_intent(
        approval, item, '{"client_order_id":"o2","type":"LIMIT","time_in_force":"FOK"}', now=NOW
    )


def test_terminal_partial_fok_blocks_next_entry(execution_repo):
    repo, _ = execution_repo
    guard = writer(repo)
    lc = LifecycleRepository(repo)
    clock = ProtectionClock(SimulationClock(NOW))
    intent = prepare(repo)
    lc.apply_entry_event(intent.intent_id, fill(qty="2"), clock)
    cmd = lc.commands(BTC)[0]
    lc.execute_fake(cmd.command_id, clock, writer=guard)
    repo.apply_event(intent.intent_id, observation("CANCELED", "2", True), protection_clock=clock)
    assert lc.get(BTC).context.protection.covered.amount == 2
    with pytest.raises(ValueError):
        another(repo)


@pytest.mark.parametrize("claimed", [False, True])
def test_released_reservation_cannot_be_sent(execution_repo, claimed):
    repo, _ = execution_repo
    intent = prepare(repo)
    guard = writer(repo)
    if claimed:
        assert repo.claim(intent.intent_id, now=NOW)
    repo.ledger.release(intent.reservation_id, observation("CANCELED", "0", True))
    exchange = FakeExchange(writer=guard)
    dispatcher = FakeDispatcher(repo, exchange, clock=lambda: NOW)
    try:
        if claimed:
            exchange.submit(repo.get_intent(intent.intent_id), repository=repo, clock=lambda: NOW)
        else:
            dispatcher.dispatch(intent.intent_id)
    except (ValueError, PermissionError):
        pass
    assert len(exchange.submissions) == 0


@pytest.mark.parametrize("elapsed", ["6", "61"])
def test_waiting_for_ownership_lock_must_recheck_clock(execution_repo, elapsed):
    repo, connection = execution_repo
    intent = prepare(repo)
    guard = writer(repo)
    simulation = SimulationClock(NOW)
    exchange = FakeExchange(writer=guard)
    dispatcher = FakeDispatcher(repo, exchange, clock=simulation.utc_now)
    lock = connection()
    lock.execute("SELECT * FROM execution_ownership WHERE account_id='a' FOR UPDATE")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(dispatcher.dispatch, intent.intent_id)
        import time

        deadline = time.monotonic() + 5
        with connection() as observer:
            observer.autocommit = True
            while True:
                observer.execute("SELECT pg_stat_clear_snapshot()")
                blocked = observer.execute(
                    "SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' "
                    "AND query LIKE '%execution_ownership%' LIMIT 1"
                ).fetchone()
                if blocked:
                    break
                if time.monotonic() >= deadline:
                    lock.rollback()
                    lock.close()
                    pytest.fail("dispatcher did not wait on ownership lock")
                time.sleep(0.01)
        simulation.advance(D(elapsed))
        lock.commit()
        lock.close()
        try:
            future.result(timeout=10)
        except (ValueError, PermissionError):
            pass
    assert simulation.utc_now() == NOW + timedelta(seconds=int(elapsed))
    assert len(exchange.submissions) == 0


@pytest.mark.parametrize("operation", ["check", "send"])
def test_copied_guard_must_not_resume_after_process_db_failure(execution_repo, operation):
    import copy

    import psycopg

    repo, _ = execution_repo
    intent = prepare(repo)
    original = writer(repo)
    copied = copy.copy(original)
    connection = original.control.connect

    def offline():
        raise psycopg.OperationalError("simulated DB loss")

    original.control.connect = offline
    with pytest.raises(psycopg.OperationalError):
        if operation == "check":
            original.check("a")
        else:
            original.send("a", lambda: pytest.fail("DB loss permitted callback"))
    original.control.connect = connection
    assert original.stopped
    exchange = FakeExchange(writer=copied)
    try:
        FakeDispatcher(repo, exchange, clock=lambda: NOW).dispatch(intent.intent_id)
    except PermissionError:
        pass
    assert len(exchange.submissions) == 0


@pytest.mark.parametrize("status", ["REJECTED", "EXPIRED"])
def test_terminal_stop_serial_emergency_remains_executable(lifecycle_repo, status):  # noqa: F811
    lifecycle, repo, _, clock = lifecycle_repo
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    guard = writer(repo)
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(qty="4"), clock)
    lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), status, terminal=True), clock)
    clock.clock.advance(D("6"))
    state = lifecycle.assess(BTC, clock)
    sells = [c for c in lifecycle.commands(BTC) if c.kind == "MARKET_SELL"]
    assert len(sells) == 1
    assert state.context.stop_status == status
    assert lifecycle.execute_fake(sells[0].command_id, clock, writer=guard)
    assert lifecycle.assess(BTC, clock).context.protection.target.amount == 0


@pytest.mark.parametrize("status", ["CANCELED", "REJECTED", "EXPIRED"])
def test_native_terminal_stop_uses_explicit_absence_profile(lifecycle_repo, status):  # noqa: F811
    from trading_bot.execution.absence import SyntheticVenueOrder
    from trading_bot.execution.fake_lifecycle import FakeLifecycleExchange

    lifecycle, repo, _, clock = lifecycle_repo
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    fake = FakeLifecycleExchange(writer=writer(repo))
    entry = fill(qty="4")
    lifecycle.apply_entry_event(prepare(repo).intent_id, entry, clock)
    stop_id = lifecycle.get(BTC).context.stop_id
    lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), status, terminal=True), clock)
    clock.clock.advance(D("6"))
    state = lifecycle.assess(BTC, clock)
    assert not [c for c in lifecycle.commands(BTC) if c.kind == "MARKET_SELL"]
    fake.observe_trade(entry)
    fake.observe_order(SyntheticVenueOrder("a", BTC, stop_id, "SELL", status))
    evidence = fake.absence(
        "a", BTC, repo.ledger.snapshot.version, state.context.protection.target, clock.utc_now()
    )
    sells = lifecycle.request_exit(state.exit_request, clock, absence=evidence)
    assert len(sells) == 1 and sells[0].kind == "MARKET_SELL"
    assert sells[0].policy == "NATIVE_LINKED_VERIFIED"
    assert lifecycle.get(BTC).context.stop_status == status
    refreshed = fake.absence(
        "a", BTC, repo.ledger.snapshot.version, state.context.protection.target, clock.utc_now()
    )
    assert fake.execute(lifecycle, sells[0].command_id, clock, absence=refreshed)
    assert lifecycle.assess(BTC, clock).context.protection.target.amount == 0


def test_partial_fok_latch_survives_flat_inventory_and_repository_restart(execution_repo):
    repo, _ = execution_repo
    guard = writer(repo)
    lifecycle = LifecycleRepository(repo)
    clock = ProtectionClock(SimulationClock(NOW))
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(qty="2"), clock)
    lifecycle.execute_fake(lifecycle.commands(BTC)[0].command_id, clock, writer=guard)
    terminal = observation("CANCELED", "2", True)
    repo.apply_event(intent.intent_id, terminal, protection_clock=clock)
    repo.ledger.release(intent.reservation_id, terminal)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    from trading_bot.domain.records import ExitRequest

    request = ExitRequest("close-partial", BTC, Quantity(D("2"), "BTC"), "test", "a", NOW)
    cancel = lifecycle.request_exit(request, clock)[0]
    lifecycle.execute_fake(cancel.command_id, clock, writer=guard)
    lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), "CANCELED", terminal=True), clock)
    sell = lifecycle.request_exit(request, clock)[0]
    lifecycle.execute_fake(sell.command_id, clock, writer=guard)
    assert lifecycle.assess(BTC, clock).context.protection.target.amount == 0
    restarted = type(repo)(repo.connect, "a", "Q")
    with pytest.raises(ValueError, match="incident|partial"):
        another(restarted)


def test_partial_terminal_evidence_latches_before_late_complete_trades(execution_repo):
    repo, _ = execution_repo
    guard = writer(repo)
    lifecycle = LifecycleRepository(repo)
    clock = ProtectionClock(SimulationClock(NOW))
    intent = prepare(repo)
    repo.apply_event(intent.intent_id, observation("CANCELED", "2", True), protection_clock=clock)
    lifecycle.apply_entry_event(intent.intent_id, fill(qty="4"), clock)
    lifecycle.execute_fake(lifecycle.commands(BTC)[0].command_id, clock, writer=guard)
    assert lifecycle.get(BTC).context.protection.covered.amount == 4
    with pytest.raises(ValueError, match="incident|partial"):
        another(repo)


def test_incident_upgrade_seeds_history_and_is_append_only(execution_repo):
    from pathlib import Path

    import psycopg

    repo, connection = execution_repo
    guard = writer(repo)
    lifecycle = LifecycleRepository(repo)
    clock = ProtectionClock(SimulationClock(NOW))
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(qty="2"), clock)
    repo.apply_event(intent.intent_id, observation("CANCELED", "2", True), protection_clock=clock)
    lifecycle.apply_entry_event(intent.intent_id, fill(trade_id="t2", qty="2"), clock)
    # Simulate the pre-002e schema; DDL is fixture-only, never a recovery API.
    with connection() as conn:
        conn.execute("DROP TABLE execution_incidents")
        conn.execute(Path("migrations/002e_execution_incidents.sql").read_text())
    with connection() as conn:
        row = conn.execute("SELECT intent_id,received_at FROM execution_incidents").fetchone()
        assert row == (intent.intent_id, None)
    for sql in (
        "DELETE FROM execution_incidents",
        "UPDATE execution_incidents SET received_at=now()",
        "TRUNCATE execution_incidents",
    ):
        with pytest.raises(psycopg.Error), connection() as conn:
            conn.execute(sql)
    with pytest.raises(ValueError, match="incident|partial"):
        another(repo)
    assert guard.token.account_id == "a"


def test_unauthenticated_guard_failure_does_not_revoke_authentic_channel(execution_repo):
    from trading_bot.execution.ownership import OwnershipLost, WriterGuard

    repo, _ = execution_repo
    authentic = writer(repo)
    forged = WriterGuard(authentic.control, authentic.token)
    with pytest.raises(OwnershipLost):
        forged.check("a")
    authentic.check("a")
    called = []
    authentic.send("a", lambda: called.append(True))
    assert called == [True]
