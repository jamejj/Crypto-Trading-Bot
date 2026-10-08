import importlib
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from accounting_helpers import BTC, NOW, D, funding, reservation
from psycopg import sql

from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import OrderObservation, RiskApproval


def execution_module(name):
    try:
        return importlib.import_module("trading_bot." + name)
    except ModuleNotFoundError:
        pytest.fail("missing P03 execution behavior: " + name)


def observation(status="ACKNOWLEDGED", qty="0", terminal=False, **changes):
    return replace(
        OrderObservation(
            "obs:" + status + ":" + qty + ":" + str(changes.get("observed_at", NOW)),
            "a",
            BTC,
            "o1",
            Quantity(D(qty), "BTC"),
            status,
            terminal,
            NOW,
        ),
        **changes,
    )


@pytest.fixture
def execution_repo():
    dsn = os.environ["P02_TEST_DSN"]
    schema = "p03_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:

        def connection():
            conn = psycopg.connect(dsn)
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.commit()
            return conn

        with connection() as conn:
            conn.execute(Path("migrations/001_ledger.sql").read_text())
        module = execution_module("storage.execution_repository")
        with connection() as conn:
            conn.execute(Path("migrations/002_execution.sql").read_text())
            conn.execute(Path("migrations/002c_writer_ownership.sql").read_text())
        repo = module.ExecutionRepository(connection, "a", "Q")
        funding(repo.ledger, "fund", Money(D("100"), "Q"))
        yield repo, connection
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def prepare(repo):
    item = reservation(repo.ledger)
    approval = RiskApproval(
        "fixture-approval",
        BTC,
        "BUY",
        Quantity(D("4"), "BTC"),
        Money(D("40"), "Q"),
        Money(D("4"), "Q"),
        Money(D("10"), "Q"),
        Money(D("9"), "Q"),
        item.ledger_version,
        "synthetic-offline",
        item.expires_at,
        False,
    )
    return repo.prepare_intent(
        approval, item, '{"client_order_id":"o1","type":"LIMIT","time_in_force":"FOK"}', now=NOW
    )


def writer(repo):
    """Explicit operator grant for legacy fake fixtures; never production auto-acquire."""
    if not hasattr(repo, "_fixture_writer"):
        m = execution_module("execution.ownership")
        control = m.OwnershipControl(repo.connect, repo.account_id, m.FakeFenceAuthority())
        repo._fixture_writer = control.manual_initial(
            "fixture", "fixture-process", "manual fixture grant"
        )
    return repo._fixture_writer
