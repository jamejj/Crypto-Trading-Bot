"""Terminal venue quantities, not partial local trade delivery, define FOK incidents."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from accounting_helpers import BTC, NOW, D, fill, funding
from execution_helpers import observation, prepare, writer
from psycopg import sql
from test_p03_corrective import another
from test_protection_lifecycle import stop_observation, verify

from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import OrderIntent
from trading_bot.execution.protection import ProtectionClock
from trading_bot.execution.reducer import OrderState, apply
from trading_bot.operations.clock import SimulationClock
from trading_bot.storage.execution_repository import ExecutionRepository
from trading_bot.storage.lifecycle_repository import LifecycleRepository


def incident_count(connection):
    with connection() as conn:
        return conn.execute("SELECT count(*) FROM execution_incidents").fetchone()[0]


@pytest.mark.parametrize("terminal_first", [True, False])
def test_full_fok_trade_delivery_is_temporary_blocker(execution_repo, terminal_first):
    repo, connection = execution_repo
    guard = writer(repo)
    lifecycle = LifecycleRepository(repo)
    clock = ProtectionClock(SimulationClock(NOW))
    intent = prepare(repo)
    terminal = observation("FILLED", "4", True)
    if terminal_first:
        repo.apply_event(intent.intent_id, terminal, protection_clock=clock)
    repo.apply_event(intent.intent_id, fill(qty="2"), protection_clock=clock)
    lifecycle.execute_fake(lifecycle.commands(BTC)[0].command_id, clock, writer=guard)
    verify(lifecycle, "SERIAL_CANCEL_THEN_MARKET_VERIFIED")
    if terminal_first:
        assert repo.assess_unresolved(clock)
        with pytest.raises(ValueError, match="unresolved"):
            another(repo)
        assert incident_count(connection) == 0
    repo.apply_event(intent.intent_id, fill(trade_id="t2", qty="2"), protection_clock=clock)
    if not terminal_first:
        repo.apply_event(intent.intent_id, terminal, protection_clock=clock)
    cancel = next(c for c in lifecycle.commands(BTC) if c.kind == "CANCEL_STOP")
    lifecycle.execute_fake(cancel.command_id, clock, writer=guard)
    lifecycle.observe_stop(stop_observation(lifecycle.get(BTC), "CANCELED", terminal=True), clock)
    lifecycle.assess(BTC, clock)
    replacement = next(
        c for c in lifecycle.commands(BTC) if c.kind == "CREATE_STOP" and c.quantity.amount == 4
    )
    lifecycle.execute_fake(replacement.command_id, clock, writer=guard)
    assert lifecycle.get(BTC).context.protection.covered.amount == 4
    assert repo.assess_unresolved(clock) == ()
    assert incident_count(connection) == 0
    restarted = type(repo)(repo.connect, "a", "Q")
    assert another(restarted).status == "PREPARED"


@pytest.mark.parametrize("status", ["CANCELED", "REJECTED", "EXPIRED"])
def test_partial_terminal_venue_evidence_survives_late_complete_trades(execution_repo, status):
    repo, connection = execution_repo
    writer(repo)
    clock = ProtectionClock(SimulationClock(NOW))
    intent = prepare(repo)
    repo.apply_event(intent.intent_id, observation(status, "2", True), protection_clock=clock)
    assert incident_count(connection) == 1
    repo.apply_event(intent.intent_id, fill(qty="2"), protection_clock=clock)
    repo.apply_event(intent.intent_id, fill(trade_id="t2", qty="2"), protection_clock=clock)
    assert incident_count(connection) == 1
    with pytest.raises(ValueError, match="incident"):
        another(type(repo)(repo.connect, "a", "Q"))


@pytest.fixture
def pre_incident_schema():
    """Create an actual old schema, never create/drop 002e to fake an upgrade."""
    dsn = os.environ["P02_TEST_DSN"]
    schema = "legacy_fok_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connection():
        conn = psycopg.connect(dsn)
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        conn.commit()
        return conn

    try:
        with connection() as conn:
            for name in (
                "001_ledger.sql",
                "002_execution.sql",
                "002b_protection_exits.sql",
                "002c_writer_ownership.sql",
                "002d_submit_windows.sql",
            ):
                conn.execute((Path("migrations") / name).read_text())
            assert conn.execute("SELECT to_regclass('execution_incidents')").fetchone() == (None,)
        yield connection
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize(
    "case,expected",
    [("partial", 1), ("partial_late_full", 1), ("full_pending_trades", 0), ("full_complete", 0)],
)
def test_pre_002e_upgrade_uses_positive_terminal_evidence(pre_incident_schema, case, expected):
    connection = pre_incident_schema
    repo = ExecutionRepository(connection, "a", "Q")
    funding(repo.ledger, "fund", Money(D("100"), "Q"))
    intent = OrderIntent(
        "i1",
        "a",
        BTC,
        "BUY",
        Quantity(D("4"), "BTC"),
        Money(D("10"), "Q"),
        "legacy-payload",
        "legacy-approval",
        "r1",
        "PREPARED",
    )
    state = OrderState("i1", "a", BTC, "o1", "BUY", intent.quantity)
    events = (
        [fill(qty="2"), observation("CANCELED", "2", True)]
        if case.startswith("partial")
        else [observation("FILLED", "4", True), fill(qty="2")]
    )
    if case in {"partial_late_full", "full_complete"}:
        events.append(fill(trade_id="t2", qty="2"))
    # Seed the old immutable event log + projection + real P02 economic journal.
    # No current entry admission is used before its required migration exists.
    with connection() as conn:
        conn.execute(
            "INSERT INTO execution_intents VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
            ("a", "i1", "o1", "legacy-approval", intent.to_json(), "{}", "{}", "legacy-payload"),
        )
        conn.execute("INSERT INTO execution_outbox VALUES ('a','i1','RESOLVED')")
        book = repo._book(conn)
        for event in events:
            version, count = book.version, len(book.entries)
            state = apply(state, event, book)
            repo._persist(conn, book, version, count)
            conn.execute(
                "INSERT INTO execution_observations(account_id,intent_id,event) "
                "VALUES ('a','i1',%s::jsonb)",
                (event.to_json(),),
            )
        conn.execute("INSERT INTO execution_orders VALUES ('a','i1',%s::jsonb)", (state.to_json(),))
    with connection() as conn:
        conn.execute(Path("migrations/002e_execution_incidents.sql").read_text())
    assert incident_count(connection) == expected
    if expected:
        with connection() as conn:
            assert conn.execute("SELECT received_at FROM execution_incidents").fetchone() == (None,)
    elif case == "full_pending_trades":
        clock = ProtectionClock(SimulationClock(NOW))
        with pytest.raises(ValueError, match="manual review"):
            repo.assess_unresolved(clock)  # legacy attempt has no invented submit window
