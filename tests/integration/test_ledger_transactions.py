"""Real PostgreSQL: crashes, duplicate writers, reservation races and durable replay."""

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from accounting_helpers import D, balance, fill, funding, reservation
from psycopg import sql

from trading_bot.domain.money import Money


@pytest.fixture
def repository():
    dsn = os.environ.get("P02_TEST_DSN")
    assert dsn, "P02_TEST_DSN must target an isolated real PostgreSQL; G2 never skips"
    schema = "p02_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        try:
            module = importlib.import_module("trading_bot.storage.ledger_repository")
        except ModuleNotFoundError:
            pytest.fail("missing durable PostgreSQL ledger repository")

        def connection():
            conn = psycopg.connect(dsn)
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.commit()
            return conn

        with connection() as conn:
            conn.execute(Path("migrations/001_ledger.sql").read_text())
        yield module.LedgerRepository(connection, "a", "Q"), connection
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_same_fill_from_concurrent_channels_posts_once(repository):
    repo, _ = repository
    funding(repo, "fund", Money(D("100"), "Q"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(repo.post_fill, [fill(), replace(fill(), source_id="rest")]))
    assert states[0] == states[1]
    assert balance(repo.snapshot, "Q") == D("80")
    assert len(repo.replay().entries) == 6


def test_concurrent_reservations_use_version_and_cannot_overspend(repository):
    repo, _ = repository
    funding(repo, "fund", Money(D("100"), "Q"))
    version = repo.snapshot.version
    request = reservation(repo, "80")

    def attempt(item):
        try:
            repo.reserve(item, version)
            return "OK"
        except ValueError:
            return "REJECTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(attempt, [request, replace(request, reservation_id="r2", order_id="o2")])
        )
    assert sorted(outcomes) == ["OK", "REJECTED"]
    assert len(repo.snapshot.reservations) == 1


def test_failure_between_postings_and_projection_rolls_back_everything(repository):
    repo, connection = repository
    funding(repo, "fund", Money(D("100"), "Q"))
    before = repo.snapshot
    # Database trigger injects a real server failure at projection write, after journal/postings.
    with connection() as conn:
        conn.execute("""CREATE FUNCTION fail_projection() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected projection failure'; END $$""")
        conn.execute("""CREATE TRIGGER failure BEFORE UPDATE ON ledger_accounts
                        FOR EACH ROW EXECUTE FUNCTION fail_projection()""")
    with pytest.raises(psycopg.Error, match="injected projection failure"):
        repo.post_fill(fill())
    assert repo.snapshot == before
    assert repo.replay().snapshot == before
    with connection() as conn:
        assert conn.execute("SELECT count(*) FROM ledger_operations").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM ledger_postings").fetchone()[0] == 2


def test_rebuild_projection_from_immutable_journal(repository):
    repo, connection = repository
    funding(repo, "fund", Money(D("100"), "Q"))
    repo.post_fill(fill())
    expected = repo.snapshot
    with connection() as conn:
        conn.execute("UPDATE ledger_accounts SET snapshot = '{}'::jsonb, version = 0")
    assert repo.rebuild() == expected
    assert repo.snapshot == expected


def test_database_rejects_history_update_delete_and_unbalanced_postings(repository):
    repo, connection = repository
    funding(repo, "fund", Money(D("100"), "Q"))
    for query in ["DELETE FROM ledger_operations", "UPDATE ledger_postings SET amount = 0"]:
        with pytest.raises(psycopg.Error, match="append-only"):
            with connection() as conn:
                conn.execute(query)
    with pytest.raises(psycopg.Error, match="unbalanced"):
        with connection() as conn:
            conn.execute("INSERT INTO ledger_postings VALUES ('a', 1, 99, 'bad', 'Q', 1)")
    assert repo.replay().snapshot == repo.snapshot


def test_autocommit_factory_still_rolls_back_journal_postings_and_projection(repository):
    repo, connection = repository
    funding(repo, "fund", Money(D("100"), "Q"))
    before = repo.snapshot
    with connection() as conn:
        conn.execute("""CREATE FUNCTION fail_projection() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected projection failure'; END $$""")
        conn.execute("""CREATE TRIGGER failure BEFORE UPDATE ON ledger_accounts
                        FOR EACH ROW EXECUTE FUNCTION fail_projection()""")

    def autocommit_connection():
        conn = connection()
        conn.autocommit = True
        return conn

    other = type(repo)(autocommit_connection, "a", "Q")
    with pytest.raises(psycopg.Error, match="injected projection failure"):
        other.post_fill(fill())
    assert other.snapshot == before
    assert other.replay().snapshot == before


def test_read_rejects_valid_but_corrupt_projection(repository):
    import json

    repo, connection = repository
    funding(repo, "fund", Money(D("100"), "Q"))
    before = repo.snapshot
    corrupted = replace(before, nav=Money(D("2"), "Q"))
    with connection() as conn:
        conn.execute(
            "UPDATE ledger_accounts SET snapshot = %s::jsonb",
            (json.dumps(json.loads(corrupted.to_json())),),
        )
    with pytest.raises(ValueError, match="projection"):
        _ = repo.snapshot
    assert repo.rebuild() == before
