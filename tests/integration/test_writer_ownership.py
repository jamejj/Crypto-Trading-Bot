from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from accounting_helpers import NOW
from execution_helpers import configure_transport, execution_module, observation, prepare


def control(repo, authority=None):
    configure_transport(repo)
    m = execution_module("execution.ownership")
    authority = authority or m.FakeFenceAuthority()
    return m.OwnershipControl(repo.connect, repo.account_id, authority), authority


def test_unowned_dispatcher_never_claims_or_sends(execution_repo):
    repo, _ = execution_repo
    intent = prepare(repo)
    m = execution_module("execution.dispatcher")
    exchange = m.FakeExchange((observation(),))
    with pytest.raises(PermissionError):
        m.FakeDispatcher(repo, exchange, clock=lambda: NOW).dispatch(intent.intent_id)
    assert exchange.submissions == []
    assert repo.get_intent(intent.intent_id).status == "PREPARED"


def test_parallel_initial_grants_have_one_writer(execution_repo):
    repo, _ = execution_repo
    ctl, authority = control(repo)
    barrier = Barrier(2)

    def grant(owner):
        barrier.wait()
        try:
            return ctl.manual_initial("runtime", owner, "explicit fixture startup")
        except PermissionError:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(grant, ["p1", "p2"]))
    winner = next(x for x in results if x is not None)
    assert sum(x is not None for x in results) == 1
    assert winner.token.epoch == 1
    assert authority.active_token("a") == winner.token
    assert ctl.current() == winner.token


def test_restart_cannot_reuse_token_or_automatically_take_over(execution_repo):
    repo, _ = execution_repo
    ctl, authority = control(repo)
    old = ctl.manual_initial("runtime", "p1", "manual startup")
    restarted, _ = control(repo, authority)
    with pytest.raises(PermissionError):
        restarted.manual_initial("runtime", "p2", "attempted restart")
    m = execution_module("execution.ownership")
    copied = m.WriterGuard(restarted, old.token)
    with pytest.raises(PermissionError):
        copied.send("a", lambda: pytest.fail("copied token transmitted"))
    assert old.check("a") is None


def test_independent_cutoff_blocks_stale_writer_even_with_valid_db(execution_repo):
    repo, _ = execution_repo
    ctl, authority = control(repo)
    old = ctl.manual_initial("runtime", "p1", "manual startup")
    authority.cut_off(old.token)
    assert ctl.current() == old.token  # DB still appears valid; external fence is decisive.
    with pytest.raises(PermissionError):
        old.send("a", lambda: pytest.fail("cut-off sender transmitted"))


def test_manual_takeover_requires_cutoff_and_durable_revoke(execution_repo):
    repo, _ = execution_repo
    ctl, authority = control(repo)
    old = ctl.manual_initial("runtime", "p1", "manual startup")
    with pytest.raises(PermissionError):
        ctl.manual_takeover(old.token, None, "runtime", "p2", "no proof")
    receipt = authority.cut_off(old.token)
    with pytest.raises(PermissionError):
        ctl.manual_takeover(old.token, receipt, "runtime", "p2", "not revoked")
    ctl.manual_revoke(old.token, receipt, "operator cutoff confirmed")
    new = ctl.manual_takeover(old.token, receipt, "runtime", "p2", "operator takeover")
    assert new.token.epoch == 2
    with pytest.raises(PermissionError):
        old.send("a", lambda: pytest.fail("old writer transmitted after takeover"))
    assert new.send("a", lambda: "sent") == "sent"


def test_lost_ownership_between_claim_and_send_preserves_unknown(execution_repo):
    repo, _ = execution_repo
    ctl, authority = control(repo)
    old = ctl.manual_initial("runtime", "p1", "manual startup")
    intent = prepare(repo)
    m = execution_module("execution.dispatcher")
    exchange = m.FakeExchange((observation(),), writer=old)

    def checkpoint(stage):
        if stage == "DISPATCHING":
            authority.cut_off(old.token)

    with pytest.raises(PermissionError):
        m.FakeDispatcher(repo, exchange, clock=lambda: NOW, checkpoint=checkpoint).dispatch(
            intent.intent_id
        )
    assert exchange.submissions == []
    assert repo.get_intent(intent.intent_id).status == "SUBMISSION_UNKNOWN"
    assert repo.ledger.snapshot.reservations[0].status == "PENDING"
    receipt = authority.cut_off(old.token)
    ctl.manual_revoke(old.token, receipt, "old process fenced")
    new = ctl.manual_takeover(old.token, receipt, "runtime", "p2", "manual takeover")
    fresh = m.FakeExchange((observation(),), writer=new)
    assert m.FakeDispatcher(repo, fresh, clock=lambda: NOW).dispatch(intent.intent_id) is False
    assert fresh.submissions == []
    assert repo.get_intent(intent.intent_id).status == "SUBMISSION_UNKNOWN"


@pytest.mark.parametrize("gateway", ["fake", "repository"])
def test_unowned_lifecycle_effect_cannot_bypass_fence(execution_repo, gateway):
    from pathlib import Path

    from accounting_helpers import BTC, D, fill

    from trading_bot.domain.money import Money
    from trading_bot.operations.clock import SimulationClock

    repo, connection = execution_repo
    with connection() as conn:
        conn.execute(Path("migrations/002b_protection_exits.sql").read_text())
    p = execution_module("execution.protection")
    lifecycle = execution_module("storage.lifecycle_repository").LifecycleRepository(repo)
    clock = p.ProtectionClock(SimulationClock(NOW))
    lifecycle.configure(p.ProtectionContext(BTC, Money(D("9"), "Q"), D("1")))
    lifecycle.apply_entry_event(prepare(repo).intent_id, fill(), clock)
    command_id = lifecycle.commands(BTC)[0].command_id
    if gateway == "fake":
        run = execution_module("execution.fake_lifecycle").FakeLifecycleExchange().execute
        with pytest.raises(PermissionError):
            run(lifecycle, command_id, clock)
    else:
        with pytest.raises(PermissionError):
            lifecycle.execute_fake(command_id, clock)
    assert lifecycle.get(BTC).context.stop_status == "PENDING"


def test_parallel_different_authorities_and_runtimes_still_one_db_grant(execution_repo):
    repo, _ = execution_repo
    first, _ = control(repo)
    second, _ = control(repo)
    barrier = Barrier(2)

    def grant(pair):
        ctl, owner = pair
        barrier.wait()
        try:
            return ctl.manual_initial(owner, owner, "manual startup")
        except PermissionError:
            return None

    with ThreadPoolExecutor(2) as pool:
        writers = list(pool.map(grant, [(first, "runtime1"), (second, "runtime2")]))
    assert sum(w is not None for w in writers) == 1


def test_two_dispatchers_only_granted_process_can_send(execution_repo):
    repo, _ = execution_repo
    ctl, authority = control(repo)
    winner = ctl.manual_initial("runtime", "p1", "manual startup")
    loser = execution_module("execution.ownership").WriterGuard(ctl, winner.token)
    intent = prepare(repo)
    m = execution_module("execution.dispatcher")
    exchanges = [m.FakeExchange((observation(),), writer=w) for w in (winner, loser)]
    barrier = Barrier(2)

    def dispatch(exchange):
        barrier.wait()
        try:
            return m.FakeDispatcher(repo, exchange, clock=lambda: NOW).dispatch(intent.intent_id)
        except PermissionError:
            return "fenced"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(dispatch, exchanges))
    assert results == [True, "fenced"]
    assert [len(e.submissions) for e in exchanges] == [1, 0]
    assert authority.active_token("a") == winner.token


def test_old_writer_with_restored_stale_db_projection_still_cannot_send(execution_repo):
    repo, connection = execution_repo
    ctl, authority = control(repo)
    old = ctl.manual_initial("runtime", "p1", "manual startup")
    receipt = authority.cut_off(old.token)
    ctl.manual_revoke(old.token, receipt, "manual cutoff")
    new = ctl.manual_takeover(old.token, receipt, "runtime", "p2", "manual takeover")
    with connection() as conn:
        conn.execute(
            "UPDATE execution_ownership SET token=%s::jsonb,status='ACTIVE'", (old.token.to_json(),)
        )
    with pytest.raises(PermissionError, match="fenced"):
        old.send("a", lambda: pytest.fail("stale lease bypassed independent fence"))
    assert authority.active_token("a") == new.token


def test_cutoff_proof_not_forgeable_or_replayable(execution_repo):
    from dataclasses import replace

    repo, _ = execution_repo
    ctl, authority = control(repo)
    old = ctl.manual_initial("runtime", "p1", "manual startup")
    receipt = authority.cut_off(old.token)
    with pytest.raises(PermissionError):
        ctl.manual_revoke(old.token, replace(receipt), "copied unsigned proof")
    ctl.manual_revoke(old.token, receipt, "genuine proof")
    ctl.manual_takeover(old.token, receipt, "runtime", "p2", "manual takeover")
    with pytest.raises(PermissionError):
        ctl.manual_takeover(old.token, receipt, "runtime", "p3", "replay")


def test_cutoff_drains_inflight_command_before_new_sender(execution_repo):
    from threading import Event

    repo, _ = execution_repo
    ctl, authority = control(repo)
    old = ctl.manual_initial("runtime", "p1", "manual startup")
    admitted, release, cutting = Event(), Event(), Event()

    def effect():
        admitted.set()
        assert release.wait(3)
        return "old admitted effect"

    def cutoff():
        cutting.set()
        return authority.cut_off(old.token)

    with ThreadPoolExecutor(2) as pool:
        sent = pool.submit(old.send, "a", effect)
        assert admitted.wait(3)
        cut = pool.submit(cutoff)
        assert cutting.wait(3)
        assert not cut.done()
        release.set()
        assert sent.result(timeout=3) == "old admitted effect"
        receipt = cut.result(timeout=3)
    ctl.manual_revoke(old.token, receipt, "inflight drained")
    new = ctl.manual_takeover(old.token, receipt, "runtime", "p2", "manual takeover")
    assert new.send("a", lambda: "new effect") == "new effect"
    with pytest.raises(PermissionError):
        old.send("a", lambda: pytest.fail("post-cutoff effect"))


def test_ownership_audit_immutable(execution_repo):
    import psycopg

    repo, connection = execution_repo
    ctl, _ = control(repo)
    ctl.manual_initial("runtime", "p1", "manual startup")
    for statement in [
        "DELETE FROM execution_ownership_audit",
        "TRUNCATE execution_ownership_audit",
    ]:
        with pytest.raises(psycopg.Error, match="immutable"):
            with connection() as conn:
                conn.execute(statement)


@pytest.mark.parametrize("gateway", ["entry", "lifecycle"])
def test_unrecognized_permissive_guard_cannot_bypass_ownership(execution_repo, gateway):
    repo, connection = execution_repo

    class AllowAnything:
        def check(self, account):
            pass

        def send(self, account, callback):
            return callback()

    intent = prepare(repo)
    if gateway == "entry":
        exchange = execution_module("execution.dispatcher").FakeExchange(writer=AllowAnything())
        with pytest.raises(PermissionError):
            exchange.submit(intent)
        assert exchange.submissions == []
    else:
        from pathlib import Path

        from accounting_helpers import BTC, D, fill

        from trading_bot.domain.money import Money
        from trading_bot.operations.clock import SimulationClock

        with connection() as conn:
            conn.execute(Path("migrations/002b_protection_exits.sql").read_text())
        p = execution_module("execution.protection")
        lifecycle = execution_module("storage.lifecycle_repository").LifecycleRepository(repo)
        clock = p.ProtectionClock(SimulationClock(NOW))
        lifecycle.configure(p.ProtectionContext(BTC, Money(D("9"), "Q"), D("1")))
        lifecycle.apply_entry_event(intent.intent_id, fill(), clock)
        with pytest.raises(PermissionError):
            lifecycle.execute_fake(
                lifecycle.commands(BTC)[0].command_id, clock, writer=AllowAnything()
            )
        assert lifecycle.get(BTC).context.stop_status == "PENDING"
