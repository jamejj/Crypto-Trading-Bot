from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from accounting_helpers import BTC, NOW, D, fill
from execution_helpers import execution_module, execution_repo, prepare, writer  # noqa: F401

from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import ExitRequest, OrderObservation
from trading_bot.operations.clock import SimulationClock


@pytest.fixture
def lifecycle_repo(execution_repo, request):  # noqa: F811
    repo, connection = execution_repo
    module = execution_module("storage.lifecycle_repository")
    with connection() as conn:
        conn.execute(Path("migrations/002b_protection_exits.sql").read_text())
    p = execution_module("execution.protection")
    lifecycle = module.LifecycleRepository(repo)
    clock = p.ProtectionClock(SimulationClock(NOW))
    lifecycle.configure(
        p.ProtectionContext(BTC, Money(D("9"), "Q"), D(getattr(request, "param", "1")))
    )
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
        BTC,
        policy,
        e.SyntheticExitVerification(
            "a",
            BTC,
            policy,
            "synthetic:fixture",
            True,
            replacement_verified=True,
            absent_stop_market_verified=True,
            replacement_artifact="synthetic:replacement-fixture",
            emergency_artifact="synthetic:emergency-fixture",
            native_atomic_emergency_verified=True,
        ),
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
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
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
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
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
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    with pytest.raises(psycopg.Error, match="effect persistence failed"):
        fake.execute(lifecycle, proposal.command_id, clock)
    assert repo.ledger.snapshot == before
    with pytest.raises(PermissionError, match="stopped"):
        fake.execute(lifecycle, proposal.command_id, clock)
    assert writer(repo).stopped
    old = writer(repo)
    ctl = old.control
    receipt = ctl.authority.cut_off(old.token)
    ctl.manual_revoke(old.token, receipt, "faulted writer manually isolated")
    replacement = ctl.manual_takeover(old.token, receipt, "fixture", "replacement", "manual")
    fresh = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=replacement)
    assert fresh.execute(lifecycle, proposal.command_id, clock) is False
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
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
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
    lifecycle, repo, clock = confirmed(lifecycle_repo)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    cancel = lifecycle.request_exit(request(), clock)[0]
    terminal = stop_observation(lifecycle.get(BTC), "CANCELED", "0", True)
    lifecycle.observe_stop(terminal, clock)
    sell = lifecycle.request_exit(request(), clock)[0]
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    fake.execute(lifecycle, cancel.command_id, clock)
    assert lifecycle.get(BTC).stop_observation == terminal
    assert fake.execute(lifecycle, sell.command_id, clock)


@pytest.mark.parametrize("status,terminal", [("CANCELED", True), ("ACTIVE", False)])
def test_observed_stop_never_replays_original_fake_create(lifecycle_repo, status, terminal):
    lifecycle, repo, _, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(), clock)
    create = lifecycle.commands(BTC)[0]
    observation = stop_observation(lifecycle.get(BTC), status, "0", terminal)
    kwargs = (
        {"side": "SELL", "trigger": Money(D("9"), "Q"), "quantity": Quantity(D("2"), "BTC")}
        if status == "ACTIVE"
        else {}
    )
    lifecycle.observe_stop(observation, clock, **kwargs)
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    assert fake.execute(lifecycle, create.command_id, clock) is False
    assert lifecycle.get(BTC).context.stop_status == (
        "CONFIRMED" if status == "ACTIVE" else "CANCELED"
    )


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


@pytest.mark.parametrize("expired", [False, True])
def test_confirmed_stop_growth_serial_replacement_or_overdue_exit(lifecycle_repo, expired):
    lifecycle, repo, _, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    lifecycle.apply_entry_event(intent.intent_id, fill("growth"), clock)
    cancel = [c for c in lifecycle.commands(BTC) if c.kind == "CANCEL_STOP"]
    assert len(cancel) == 1
    deadline = lifecycle.get(BTC).serial_deadline
    assert deadline == NOW + timedelta(seconds=5)
    if expired:
        clock.clock.advance(D("6"))
    lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), "CANCELED", "0", True), clock)
    state = lifecycle.assess(BTC, clock)
    latest = lifecycle.commands(BTC)
    if expired:
        sells = [c for c in latest if c.kind == "MARKET_SELL"]
        assert len(sells) == 1 and sells[0].quantity.amount == 4
        assert len([c for c in latest if c.kind == "CREATE_STOP"]) == 1
    else:
        creates = [c for c in latest if c.kind == "CREATE_STOP"]
        assert len(creates) == 2
        replacement = next(c for c in creates if c.quantity.amount == 4)
        assert state.context.stop_id == replacement.command_id
        assert state.serial_deadline == deadline
        assert (
            execution_module("execution.fake_lifecycle")
            .FakeLifecycleExchange(writer=writer(repo))
            .execute(lifecycle, replacement.command_id, clock)
        )
        assert lifecycle.get(BTC).context.protection.covered.amount == 4
    restarted = type(lifecycle)(type(repo)(repo.connect, "a", "Q"))
    restarted.assess(BTC, clock)
    assert len(restarted.commands(BTC)) == len(latest)


@pytest.mark.parametrize("policy", ["SERIAL_CANCEL_THEN_MARKET_VERIFIED", "NATIVE_LINKED_VERIFIED"])
@pytest.mark.parametrize("lifecycle_repo", ["5"], indirect=True)
def test_overdue_below_protection_minimum_positive_absence_emergency(lifecycle_repo, policy):
    lifecycle, repo, _, clock = lifecycle_repo
    entry = fill(qty="2")
    lifecycle.apply_entry_event(prepare(repo).intent_id, entry, clock)
    verify(lifecycle, policy)
    clock.clock.advance(D("6"))
    state = lifecycle.assess(BTC, clock)
    assert state.exit_request is not None
    assert lifecycle.commands(BTC) == []
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    fake.observe_trade(entry)
    evidence = fake.absence(
        "a", BTC, repo.ledger.snapshot.version, Quantity(D("2"), "BTC"), clock.utc_now()
    )
    sell = lifecycle.request_exit(state.exit_request, clock, absence=evidence)[0]
    assert sell.kind == "MARKET_SELL" and sell.quantity.amount == 2
    assert lifecycle.get(BTC).stop_observation is None
    fresh = fake.absence(
        "a", BTC, repo.ledger.snapshot.version, Quantity(D("2"), "BTC"), clock.utc_now()
    )
    assert fake.execute(lifecycle, sell.command_id, clock, absence=fresh)
    assert repo.ledger.snapshot.inventory == ()
    assert fake.execute(lifecycle, sell.command_id, clock, absence=fresh) is False
    assert lifecycle.assess(BTC, clock).incident != "BLOCKED_BELOW_EXIT_MINIMUM"


def test_native_replacement_requires_own_verification_and_atomic_right_transfer(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    lifecycle.apply_entry_event(intent.intent_id, fill("nativegrowth"), clock)
    replacements = [c for c in lifecycle.commands(BTC) if c.kind == "NATIVE_REPLACE_STOP"]
    assert len(replacements) == 1
    proposal = replacements[0]
    assert proposal.quantity.amount == 4
    rights = [
        r for r in repo.ledger.snapshot.reservations if r.side == "SELL" and r.status == "PENDING"
    ]
    assert sum(r.quantity.amount for r in rights) == 4
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    assert fake.execute(lifecycle, proposal.command_id, clock)
    state = lifecycle.get(BTC)
    assert state.context.protection.covered.amount == 4
    assert state.context.stop_id != proposal.stop_id
    rights = [
        r for r in repo.ledger.snapshot.reservations if r.side == "SELL" and r.status == "PENDING"
    ]
    assert len(rights) == 1 and rights[0].quantity.amount == 4
    assert fake.execute(lifecycle, proposal.command_id, clock) is False


def test_native_overdue_undersized_stop_atomic_emergency(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    clock.clock.advance(D("6"))
    lifecycle.apply_entry_event(intent.intent_id, fill("overduegrowth"), clock)
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    clock.clock.advance(D("6"))
    state = lifecycle.assess(BTC, clock)
    assert state.exit_request is not None
    commands = [c for c in lifecycle.commands(BTC) if c.kind == "NATIVE_EMERGENCY_EXIT"]
    assert len(commands) == 1
    assert (
        execution_module("execution.fake_lifecycle")
        .FakeLifecycleExchange(writer=writer(repo))
        .execute(lifecycle, commands[0].command_id, clock)
    )
    assert lifecycle.get(BTC).context.protection.target.amount == 0
    assert repo.ledger.snapshot.inventory == ()


@pytest.mark.parametrize("policy", ["SERIAL_CANCEL_THEN_MARKET_VERIFIED", "NATIVE_LINKED_VERIFIED"])
def test_completed_replacement_deadline_does_not_reopen_emergency(lifecycle_repo, policy):
    lifecycle, repo, _, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    verify(lifecycle, policy)
    lifecycle.apply_entry_event(intent.intent_id, fill("growth"), clock)
    if policy.startswith("SERIAL"):
        lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), "CANCELED", "0", True), clock)
        state = lifecycle.assess(BTC, clock)
        command_id = state.context.stop_id
    else:
        command_id = next(
            c.command_id for c in lifecycle.commands(BTC) if c.kind == "NATIVE_REPLACE_STOP"
        )
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    assert fake.execute(lifecycle, command_id, clock)
    count = len(lifecycle.commands(BTC))
    clock.clock.advance(D("6"))
    state = lifecycle.assess(BTC, clock)
    assert state.context.protection.covered.amount == 4
    assert state.exit_request is None
    assert len(lifecycle.commands(BTC)) == count


@pytest.mark.parametrize("lifecycle_repo", ["5"], indirect=True)
@pytest.mark.parametrize(
    "bad",
    [
        "account",
        "instrument",
        "quantity",
        "version",
        "incomplete_orders",
        "incomplete_trades",
        "old",
        "future",
        "foreign_source",
        "missing_trade",
        "unknown_order",
        "other_market_sell",
        "invalid_side",
        "negative_sequence",
        "unresolved_command",
    ],
)
def test_no_stop_absence_malformed_stale_or_ambiguous_blocks(lifecycle_repo, bad):
    lifecycle, repo, connection, clock = lifecycle_repo
    entry = fill(qty="2")
    lifecycle.apply_entry_event(prepare(repo).intent_id, entry, clock)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    clock.clock.advance(D("6"))
    request = lifecycle.assess(BTC, clock).exit_request
    fake_module = execution_module("execution.fake_lifecycle")
    evidence_module = execution_module("execution.absence")
    fake = fake_module.FakeLifecycleExchange(writer=writer(repo))
    fake.observe_trade(entry)
    evidence = fake.absence(
        "a", BTC, repo.ledger.snapshot.version, Quantity(D("2"), "BTC"), clock.utc_now()
    )
    if bad == "account":
        evidence = replace(evidence, account_id="other")
    elif bad == "instrument":
        evidence = replace(evidence, instrument=replace(BTC, market="BTC_ALT_Q"))
    elif bad == "quantity":
        evidence = replace(evidence, quantity=Quantity(D("1"), "BTC"))
    elif bad == "version":
        evidence = replace(evidence, ledger_version=evidence.ledger_version - 1)
    elif bad == "incomplete_orders":
        evidence = replace(evidence, snapshot=replace(evidence.snapshot, orders_complete=False))
    elif bad == "incomplete_trades":
        evidence = replace(evidence, snapshot=replace(evidence.snapshot, trades_complete=False))
    elif bad == "old":
        evidence = replace(evidence, snapshot=replace(evidence.snapshot, observed_at=NOW))
    elif bad == "future":
        evidence = replace(
            evidence,
            snapshot=replace(evidence.snapshot, observed_at=clock.utc_now() + timedelta(seconds=1)),
        )
    elif bad == "foreign_source":
        evidence = replace(
            evidence, snapshot=replace(evidence.snapshot, source_id="synthetic:other")
        )
    elif bad == "missing_trade":
        evidence = replace(evidence, snapshot=replace(evidence.snapshot, trades=()))
    elif bad == "negative_sequence":
        evidence = replace(evidence, snapshot=replace(evidence.snapshot, sequence=-1))
    elif bad in {"unknown_order", "other_market_sell", "invalid_side"}:
        instrument = replace(BTC, market="BTC_ALT_Q") if bad == "other_market_sell" else BTC
        order = evidence_module.SyntheticVenueOrder(
            "a",
            instrument,
            "competing",
            "invalid" if bad == "invalid_side" else "SELL",
            "UNKNOWN" if bad == "unknown_order" else "ACTIVE",
        )
        evidence = replace(evidence, snapshot=replace(evidence.snapshot, orders=(order,)))
    elif bad == "unresolved_command":
        with connection() as conn:
            conn.execute("UPDATE execution_outbox SET state='SUBMISSION_UNKNOWN'")
    before = repo.ledger.snapshot
    assert lifecycle.request_exit(request, clock, absence=evidence) == []
    assert lifecycle.get(BTC).incident == "BLOCKED_ABSENCE_EVIDENCE"
    assert repo.ledger.snapshot == before
    assert lifecycle.commands(BTC) == []


@pytest.mark.parametrize("lifecycle_repo", ["5"], indirect=True)
def test_below_both_minima_records_dust_incident_without_sell(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(qty="0.5"), clock)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    clock.clock.advance(D("6"))
    state = lifecycle.assess(BTC, clock)
    assert state.incident == "BLOCKED_BELOW_EXIT_MINIMUM"
    assert state.exit_request.deadline == NOW + timedelta(seconds=5)
    assert state.context.protection.uncovered.amount == D("0.5")
    assert lifecycle.commands(BTC) == []


@pytest.mark.parametrize("policy", ["SERIAL_CANCEL_THEN_MARKET_VERIFIED", "NATIVE_LINKED_VERIFIED"])
def test_stop_fill_during_replacement_preserves_fresh_inventory_and_no_oversell(
    lifecycle_repo, policy
):
    lifecycle, repo, _, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    verify(lifecycle, policy)
    lifecycle.apply_entry_event(intent.intent_id, fill("growth"), clock)
    state = lifecycle.get(BTC)
    lifecycle.apply_stop_fill(
        replace(fill("race", side="SELL", qty="1"), order_id=state.context.stop_id), clock
    )
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    if policy.startswith("SERIAL"):
        lifecycle.observe_stop(stop_observation(state, "CANCELED", "1", True), clock)
        state = lifecycle.assess(BTC, clock)
        assert state.context.stop_quantity.amount == 3
        assert fake.execute(lifecycle, state.context.stop_id, clock)
        assert lifecycle.get(BTC).context.protection.covered.amount == 3
    else:
        proposal = next(c for c in lifecycle.commands(BTC) if c.kind == "NATIVE_REPLACE_STOP")
        with pytest.raises(ValueError, match="stale native"):
            fake.execute(lifecycle, proposal.command_id, clock)
        assert lifecycle.get(BTC).context.stop_status == "UNKNOWN"
        assert fake.execute(lifecycle, proposal.command_id, clock) is False
    assert sum(lot.quantity.amount for lot in repo.ledger.snapshot.inventory) == 3


@pytest.mark.parametrize("lifecycle_repo", ["5"], indirect=True)
def test_absence_generation_change_before_effect_leaves_sell_unknown(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    entry = fill(qty="2")
    lifecycle.apply_entry_event(prepare(repo).intent_id, entry, clock)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    clock.clock.advance(D("6"))
    request = lifecycle.assess(BTC, clock).exit_request
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    fake.observe_trade(entry)
    evidence = fake.absence(
        "a", BTC, repo.ledger.snapshot.version, Quantity(D("2"), "BTC"), clock.utc_now()
    )
    proposal = lifecycle.request_exit(request, clock, absence=evidence)[0]
    fresh = fake.absence(
        "a", BTC, repo.ledger.snapshot.version, Quantity(D("2"), "BTC"), clock.utc_now()
    )
    order = execution_module("execution.absence").SyntheticVenueOrder(
        "a", BTC, "late", "SELL", "ACTIVE"
    )
    fake.observe_order(order)
    with pytest.raises(ValueError, match="generation"):
        fake.execute(lifecycle, proposal.command_id, clock, absence=fresh)
    assert repo.ledger.snapshot.inventory
    assert lifecycle.get(BTC).sell_status == "PENDING"
    # Latest positive observation is not absence and must block after durable UNKNOWN claim.
    fresh = fake.absence(
        "a", BTC, repo.ledger.snapshot.version, Quantity(D("2"), "BTC"), clock.utc_now()
    )
    with pytest.raises(ValueError, match="positive absence"):
        fake.execute(lifecycle, proposal.command_id, clock, absence=fresh)
    assert lifecycle.get(BTC).sell_status == "UNKNOWN"
    assert fake.execute(lifecycle, proposal.command_id, clock, absence=fresh) is False


@pytest.mark.parametrize("policy", ["SERIAL_CANCEL_THEN_MARKET_VERIFIED", "NATIVE_LINKED_VERIFIED"])
def test_replacement_intent_failure_rolls_back_new_fill_and_right(lifecycle_repo, policy):
    lifecycle, repo, connection, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    verify(lifecycle, policy)
    before = repo.ledger.snapshot
    state_before = lifecycle.get(BTC)
    with connection() as conn:
        conn.execute("""CREATE FUNCTION fail_replacement() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'replacement persistence failed'; END $$""")
        conn.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON lifecycle_commands "
            "FOR EACH ROW EXECUTE FUNCTION fail_replacement()"
        )
    with pytest.raises(psycopg.Error, match="replacement persistence failed"):
        lifecycle.apply_entry_event(intent.intent_id, fill("growth"), clock)
    assert repo.ledger.snapshot == before
    assert lifecycle.get(BTC) == state_before
    assert len(lifecycle.commands(BTC)) == 1


def test_native_atomic_effect_failure_rolls_back_right_transfer_and_keeps_unknown(lifecycle_repo):
    lifecycle, repo, connection, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    lifecycle.apply_entry_event(intent.intent_id, fill("growth"), clock)
    proposal = next(c for c in lifecycle.commands(BTC) if c.kind == "NATIVE_REPLACE_STOP")
    before = repo.ledger.snapshot
    with connection() as conn:
        conn.execute("""CREATE FUNCTION fail_transfer() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'transfer persistence failed'; END $$""")
        conn.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON lifecycle_commands "
            "FOR EACH ROW EXECUTE FUNCTION fail_transfer()"
        )
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    with pytest.raises(psycopg.Error, match="transfer persistence failed"):
        fake.execute(lifecycle, proposal.command_id, clock)
    assert repo.ledger.snapshot == before
    assert lifecycle.get(BTC).context.stop_status == "UNKNOWN"
    with pytest.raises(PermissionError, match="stopped"):
        fake.execute(lifecycle, proposal.command_id, clock)
    assert writer(repo).stopped
    old = writer(repo)
    ctl = old.control
    receipt = ctl.authority.cut_off(old.token)
    ctl.manual_revoke(old.token, receipt, "faulted writer manually isolated")
    replacement = ctl.manual_takeover(old.token, receipt, "fixture", "replacement", "manual")
    fresh = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=replacement)
    assert fresh.execute(lifecycle, proposal.command_id, clock) is False


@pytest.mark.parametrize("lifecycle_repo", ["5"], indirect=True)
def test_absent_emergency_intent_failure_rolls_back_reservation(lifecycle_repo):
    lifecycle, repo, connection, clock = lifecycle_repo
    entry = fill(qty="2")
    lifecycle.apply_entry_event(prepare(repo).intent_id, entry, clock)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    clock.clock.advance(D("6"))
    request = lifecycle.assess(BTC, clock).exit_request
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    fake.observe_trade(entry)
    evidence = fake.absence(
        "a", BTC, repo.ledger.snapshot.version, Quantity(D("2"), "BTC"), clock.utc_now()
    )
    before = repo.ledger.snapshot
    state_before = lifecycle.get(BTC)
    with connection() as conn:
        conn.execute("""CREATE FUNCTION fail_emergency() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'emergency persistence failed'; END $$""")
        conn.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON lifecycle_commands "
            "FOR EACH ROW EXECUTE FUNCTION fail_emergency()"
        )
    with pytest.raises(psycopg.Error, match="emergency persistence failed"):
        lifecycle.request_exit(request, clock, absence=evidence)
    assert repo.ledger.snapshot == before
    assert lifecycle.get(BTC) == state_before
    assert lifecycle.commands(BTC) == []


def test_pending_native_replacement_expiry_never_executes_or_parallel_sells(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    lifecycle.apply_entry_event(intent.intent_id, fill("growth"), clock)
    proposal = next(c for c in lifecycle.commands(BTC) if c.kind == "NATIVE_REPLACE_STOP")
    clock.clock.advance(D("6"))
    state = lifecycle.assess(BTC, clock)
    assert state.exit_request is not None
    assert not any(
        c.kind in {"MARKET_SELL", "NATIVE_EMERGENCY_EXIT"} for c in lifecycle.commands(BTC)
    )
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    with pytest.raises(ValueError, match="deadline"):
        fake.execute(lifecycle, proposal.command_id, clock)
    assert lifecycle.get(BTC).context.stop_status == "UNKNOWN"
    assert fake.execute(lifecycle, proposal.command_id, clock) is False
    assert sum(lot.quantity.amount for lot in repo.ledger.snapshot.inventory) == 4


def test_native_oversized_stop_after_late_fee_replaces_net_without_negative_right(lifecycle_repo):
    from accounting_helpers import fee

    lifecycle, repo, _, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    repo.ledger.post_fill(
        replace(
            fill("collateral", qty="0.2"),
            instrument=replace(BTC, market="BTC_ALT_Q"),
            order_id="collateral-order",
        )
    )
    repo.ledger.post_fee_correction(fee())
    state = lifecycle.assess(BTC, clock)
    assert state.context.protection.target.amount == D("1.9")
    proposal = next(c for c in lifecycle.commands(BTC) if c.kind == "NATIVE_REPLACE_STOP")
    assert proposal.quantity.amount == D("1.9")
    assert (
        execution_module("execution.fake_lifecycle")
        .FakeLifecycleExchange(writer=writer(repo))
        .execute(lifecycle, proposal.command_id, clock)
    )
    assert lifecycle.get(BTC).context.protection.covered.amount == D("1.9")


def test_native_emergency_oversized_stop_uses_net_and_no_negative_increment(lifecycle_repo):
    from accounting_helpers import fee

    lifecycle, repo, _, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(), clock)
    # Fee arrives while original deadline is still open; ACTIVE becomes UNKNOWN because
    # its evidence claims a quantity exceeding the net target, so confirm before correction.
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    repo.ledger.post_fill(
        replace(
            fill("collateral", qty="0.2"),
            instrument=replace(BTC, market="BTC_ALT_Q"),
            order_id="collateral-order",
        )
    )
    repo.ledger.post_fee_correction(fee())
    # Explicit disabled management assessment records CONFLICT and the uncovered timer.
    state = lifecycle.assess(BTC, clock)
    assert state.context.protection.status == "CONFLICT"
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    clock.clock.advance(D("6"))
    state = lifecycle.assess(BTC, clock)
    assert state.exit_request is not None
    proposal = next(c for c in lifecycle.commands(BTC) if c.kind == "NATIVE_EMERGENCY_EXIT")
    assert proposal.quantity.amount == D("1.9")
    assert (
        execution_module("execution.fake_lifecycle")
        .FakeLifecycleExchange(writer=writer(repo))
        .execute(lifecycle, proposal.command_id, clock)
    )
    assert all(lot.instrument != BTC for lot in repo.ledger.snapshot.inventory)


@pytest.mark.parametrize("lifecycle_repo", ["3"], indirect=True)
def test_native_below_protection_minimum_waits_for_legal_emergency(lifecycle_repo):
    from accounting_helpers import fee

    lifecycle, repo, _, clock = lifecycle_repo
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(qty="4"), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("4"), "BTC"),
    )
    verify(lifecycle, "NATIVE_LINKED_VERIFIED")
    repo.ledger.post_fill(
        replace(
            fill("collateral", qty="1.2"),
            instrument=replace(BTC, market="BTC_ALT_Q"),
            order_id="collateral-order",
        )
    )
    repo.ledger.post_fee_correction(fee("1.1"))
    state = lifecycle.assess(BTC, clock)
    assert state.incident == "BLOCKED_REPLACEMENT_MINIMUM"
    assert not any(c.kind == "NATIVE_REPLACE_STOP" for c in lifecycle.commands(BTC))
    clock.clock.advance(D("6"))
    state = lifecycle.assess(BTC, clock)
    proposal = next(c for c in lifecycle.commands(BTC) if c.kind == "NATIVE_EMERGENCY_EXIT")
    assert proposal.quantity.amount == D("2.9")
    assert (
        execution_module("execution.fake_lifecycle")
        .FakeLifecycleExchange(writer=writer(repo))
        .execute(lifecycle, proposal.command_id, clock)
    )
    assert all(lot.instrument != BTC for lot in repo.ledger.snapshot.inventory)


def test_fake_venue_store_records_create_and_cancel_ack_without_terminal_absence(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    entry = fill(qty="4")
    lifecycle.apply_entry_event(prepare(repo).intent_id, entry, clock)
    create = lifecycle.commands(BTC)[0]
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    fake.observe_trade(entry)
    before = fake.snapshot("a", "BTC", clock.utc_now())
    assert fake.execute(lifecycle, create.command_id, clock)
    active = fake.snapshot("a", "BTC", clock.utc_now())
    assert active.sequence > before.sequence
    assert [(o.order_id, o.status) for o in active.orders] == [(create.command_id, "ACTIVE")]
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    cancel = lifecycle.request_exit(request(), clock)[0]
    assert fake.execute(lifecycle, cancel.command_id, clock)
    acknowledged = fake.snapshot("a", "BTC", clock.utc_now())
    assert acknowledged.sequence > active.sequence
    assert [(o.order_id, o.status) for o in acknowledged.orders] == [
        (create.command_id, "CANCEL_PENDING")
    ]
    assert acknowledged.trades == (entry,)
    assert fake.execute(lifecycle, cancel.command_id, clock) is False
    assert fake.snapshot("a", "BTC", clock.utc_now()) == acknowledged


def test_serial_replacement_delayed_dispatch_cannot_reissue_after_deadline(lifecycle_repo):
    lifecycle, repo, _, clock = lifecycle_repo
    intent = prepare(repo)
    lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
    lifecycle.observe_stop(
        stop_observation(lifecycle.get(BTC)),
        clock,
        side="SELL",
        trigger=Money(D("9"), "Q"),
        quantity=Quantity(D("2"), "BTC"),
    )
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    lifecycle.apply_entry_event(intent.intent_id, fill("growth"), clock)
    lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), "CANCELED", "0", True), clock)
    state = lifecycle.assess(BTC, clock)
    proposal_id = state.context.stop_id
    clock.clock.advance(D("6"))
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    with pytest.raises(ValueError, match="deadline"):
        fake.execute(lifecycle, proposal_id, clock)
    state = lifecycle.assess(BTC, clock)
    assert state.context.stop_status == "UNKNOWN"
    assert state.exit_request is not None
    assert not any(c.kind == "MARKET_SELL" for c in lifecycle.commands(BTC))
    assert fake.execute(lifecycle, proposal_id, clock) is False


def test_partial_serial_exit_returned_cancel_matches_durable_command(lifecycle_repo):
    lifecycle, repo, clock = confirmed(lifecycle_repo)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    partial = replace(request(), quantity=Quantity(D("2"), "BTC"))
    cancel = lifecycle.request_exit(partial, clock)[0]
    assert cancel in lifecycle.commands(BTC)
    fake = execution_module("execution.fake_lifecycle").FakeLifecycleExchange(writer=writer(repo))
    assert fake.execute(lifecycle, cancel.command_id, clock)
    lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), "CANCELED", "0", True), clock)
    sell = lifecycle.request_exit(partial, clock)[0]
    assert sell.quantity.amount == 2
    assert fake.execute(lifecycle, sell.command_id, clock)
    state = lifecycle.assess(BTC, clock)
    assert state.context.protection.target.amount == 2
    assert state.incident == "BLOCKED_RESIDUAL_AFTER_EXIT"
