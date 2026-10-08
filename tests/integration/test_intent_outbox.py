import psycopg
import pytest
from execution_helpers import prepare  # noqa: F401


def test_preparation_persists_reservation_intent_and_exact_payload(execution_repo):
    repo, connection = execution_repo
    intent = prepare(repo)
    assert intent.status == "PREPARED"
    with connection() as conn:
        row = conn.execute("SELECT client_order_id, payload FROM execution_intents").fetchone()
        assert row == ("o1", '{"client_order_id":"o1","type":"LIMIT","time_in_force":"FOK"}')
        assert conn.execute("SELECT state FROM execution_outbox").fetchone() == ("PREPARED",)
    assert repo.ledger.snapshot.reservations[0].cash.amount == 40


def test_outbox_failure_rolls_back_intent_and_reservation(execution_repo):
    repo, connection = execution_repo
    before = repo.ledger.snapshot
    with connection() as conn:
        conn.execute("""CREATE FUNCTION fail_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'outbox failed'; END $$""")
        conn.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON execution_outbox "
            "FOR EACH ROW EXECUTE FUNCTION fail_outbox()"
        )
    with pytest.raises(psycopg.Error, match="outbox failed"):
        prepare(repo)
    assert repo.ledger.snapshot == before
    with connection() as conn:
        assert conn.execute("SELECT count(*) FROM execution_intents").fetchone() == (0,)


def test_database_rejects_mutating_durable_identity(execution_repo):
    repo, connection = execution_repo
    prepare(repo)
    with pytest.raises(psycopg.Error, match="immutable"):
        with connection() as conn:
            conn.execute("UPDATE execution_intents SET payload = '{}' ")


def test_unknown_create_blocks_another_account_entry(execution_repo):
    from dataclasses import replace

    from accounting_helpers import BTC, NOW, D, reservation

    from trading_bot.domain.money import Money, Quantity
    from trading_bot.domain.records import RiskApproval

    repo, _ = execution_repo
    first = prepare(repo)
    repo.claim(first.intent_id, now=NOW)
    repo.recover_ambiguous()
    item = replace(reservation(repo.ledger), intent_id="i2", reservation_id="r2", order_id="o2")
    approval = RiskApproval(
        "a2",
        BTC,
        "BUY",
        Quantity(D("4"), "BTC"),
        Money(D("40"), "Q"),
        Money(D("4"), "Q"),
        Money(D("10"), "Q"),
        Money(D("9"), "Q"),
        item.ledger_version,
        "offline",
        item.expires_at,
        False,
    )
    with pytest.raises(ValueError, match="unresolved"):
        repo.prepare_intent(
            approval, item, '{"client_order_id":"o2","type":"LIMIT","time_in_force":"FOK"}', now=NOW
        )


@pytest.mark.parametrize("change", ["expired", "cash", "side", "payload", "consumed"])
def test_invalid_fixture_authority_never_prepares(execution_repo, change):
    from dataclasses import replace
    from datetime import timedelta

    from accounting_helpers import BTC, NOW, D, reservation

    from trading_bot.domain.money import Money, Quantity
    from trading_bot.domain.records import RiskApproval

    repo, connection = execution_repo
    item = reservation(repo.ledger)
    approval = RiskApproval(
        "fixture",
        BTC,
        "BUY",
        Quantity(D("4"), "BTC"),
        Money(D("40"), "Q"),
        Money(D("4"), "Q"),
        Money(D("10"), "Q"),
        Money(D("9"), "Q"),
        item.ledger_version,
        "offline",
        item.expires_at,
        False,
    )
    payload = '{"client_order_id":"o1","type":"LIMIT","time_in_force":"FOK"}'
    if change == "expired":
        approval = replace(approval, expires_at=NOW - timedelta(seconds=1))
    elif change == "cash":
        approval = replace(approval, max_cash=Money(D("39"), "Q"))
    elif change == "side":
        approval = replace(approval, side="SELL")
    elif change == "consumed":
        approval = replace(approval, consumed=True)
    else:
        payload = '{"client_order_id":"wrong","type":"MARKET"}'
    before = repo.ledger.snapshot
    with pytest.raises(ValueError):
        repo.prepare_intent(approval, item, payload, now=NOW)
    assert repo.ledger.snapshot == before
    with connection() as conn:
        assert conn.execute("SELECT count(*) FROM execution_intents").fetchone() == (0,)


def test_fill_and_projection_persist_together_and_replay_once(execution_repo):
    from dataclasses import replace

    from accounting_helpers import balance, fill
    from execution_helpers import observation

    repo, _ = execution_repo
    intent = prepare(repo)
    repo.apply_event(intent.intent_id, fill())
    repo.apply_event(intent.intent_id, observation())
    repo.apply_event(intent.intent_id, replace(fill(), source_id="rest"))
    restarted = type(repo)(repo.connect, "a", "Q")
    assert restarted.get_state(intent.intent_id).filled.amount == 2
    assert balance(restarted.ledger.snapshot, "BTC") == 2
    assert restarted.get_state(intent.intent_id).status == "PARTIALLY_FILLED"


@pytest.mark.parametrize("autocommit", [False, True])
def test_fill_economics_and_order_projection_roll_back_together(execution_repo, autocommit):
    from accounting_helpers import fill

    repo, connection = execution_repo
    intent = prepare(repo)
    before_ledger = repo.ledger.snapshot
    before_order = repo.get_state(intent.intent_id)
    with connection() as conn:
        conn.execute("""CREATE FUNCTION fail_order() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM ledger_operations WHERE method='_fill') THEN
                RAISE EXCEPTION 'fault not reached after durable fill';
              END IF;
              RAISE EXCEPTION 'order projection failed';
            END $$""")
        conn.execute(
            "CREATE TRIGGER fail BEFORE UPDATE ON execution_orders "
            "FOR EACH ROW EXECUTE FUNCTION fail_order()"
        )

    def factory():
        conn = connection()
        conn.autocommit = autocommit
        return conn

    writer = type(repo)(factory, "a", "Q")
    with pytest.raises(psycopg.Error, match="order projection failed"):
        writer.apply_event(intent.intent_id, fill())
    assert writer.ledger.snapshot == before_ledger
    assert writer.ledger.replay().snapshot == before_ledger
    assert writer.get_state(intent.intent_id) == before_order
    with connection() as conn:
        assert conn.execute("SELECT count(*) FROM execution_observations").fetchone() == (0,)
