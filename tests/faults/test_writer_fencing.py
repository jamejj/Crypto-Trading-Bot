from concurrent.futures import ThreadPoolExecutor
from threading import Event

import psycopg
import pytest
from execution_helpers import execution_module


def control(repo, checkpoint=None):
    m = execution_module("execution.ownership")
    authority = m.FakeFenceAuthority()
    ctl = m.OwnershipControl(repo.connect, "a", authority, checkpoint=checkpoint)
    return ctl, authority


@pytest.mark.parametrize(
    "boundary, durable, active",
    [
        ("BEFORE_RECORD", False, False),
        ("RECORDED_UNCOMMITTED", False, False),
        ("RECORD_COMMITTED", True, False),
        ("CHANNEL_ACTIVE", True, True),
    ],
)
def test_crash_grant_boundaries_never_auto_recover(execution_repo, boundary, durable, active):
    repo, _ = execution_repo

    class Crash(BaseException):
        pass

    def checkpoint(stage):
        if stage == boundary:
            raise Crash()

    ctl, authority = control(repo, checkpoint)
    with pytest.raises(Crash):
        ctl.manual_initial("runtime", "crashed", "manual startup")
    token = ctl.current()
    assert (token is not None) is durable
    assert (authority.active_token("a") is not None) is active
    if durable:
        ctl.checkpoint = lambda stage: None
        with pytest.raises(PermissionError):
            ctl.manual_initial("runtime", "replacement", "no cutoff")
        receipt = authority.cut_off(token)
        ctl.manual_revoke(token, receipt, "operator isolation")
        replacement = ctl.manual_takeover(token, receipt, "runtime", "replacement", "manual")
        assert replacement.token.epoch == 2


def test_db_failure_latches_stop_even_after_connection_recovers(execution_repo):
    repo, _ = execution_repo
    ctl, _ = control(repo)
    writer = ctl.manual_initial("runtime", "p1", "manual startup")
    original = ctl.connect

    def failed():
        raise psycopg.OperationalError("synthetic DB unavailable")

    ctl.connect = failed
    with pytest.raises(psycopg.OperationalError):
        writer.send("a", lambda: pytest.fail("offline DB transmitted"))
    ctl.connect = original
    with pytest.raises(PermissionError):
        writer.send("a", lambda: pytest.fail("stopped writer automatically resumed"))


def test_cutoff_then_revoke_has_no_db_authority_lock_inversion(execution_repo):
    repo, connection = execution_repo
    ctl, authority = control(repo)
    writer = ctl.manual_initial("runtime", "p1", "manual startup")
    receipt = authority.cut_off(writer.token)
    entered, release, revoke_connect = Event(), Event(), Event()
    original = authority._send

    def paused(*args):
        entered.set()  # writer holds the ownership row, before the authority lock.
        assert release.wait(3)
        return original(*args)

    def revoke_factory():
        conn = connection()
        conn.execute("SET lock_timeout = '1000ms'")
        conn.commit()
        revoke_connect.set()
        return conn

    authority._send = paused
    ctl2 = execution_module("execution.ownership").OwnershipControl(revoke_factory, "a", authority)
    with ThreadPoolExecutor(2) as pool:
        sender = pool.submit(writer.send, "a", lambda: pytest.fail("cutoff sent"))
        assert entered.wait(3)
        revoker = pool.submit(ctl2.manual_revoke, writer.token, receipt, "manual revoke")
        assert revoke_connect.wait(3)
        release.set()
        with pytest.raises(PermissionError):
            sender.result(timeout=3)
        assert revoker.result(timeout=3) is None


@pytest.mark.parametrize("boundary", ["RECORDED_UNCOMMITTED", "RECORD_COMMITTED", "CHANNEL_ACTIVE"])
def test_crash_manual_takeover_keeps_old_fenced_and_requires_new_cutoff(execution_repo, boundary):
    repo, _ = execution_repo
    ctl, authority = control(repo)
    old = ctl.manual_initial("runtime", "p1", "manual startup")
    receipt = authority.cut_off(old.token)
    ctl.manual_revoke(old.token, receipt, "manual cutoff")

    class Crash(BaseException):
        pass

    def checkpoint(stage):
        if stage == boundary:
            raise Crash()

    ctl.checkpoint = checkpoint
    with pytest.raises(Crash):
        ctl.manual_takeover(old.token, receipt, "runtime", "p2", "manual takeover")
    with pytest.raises(PermissionError):
        old.send("a", lambda: pytest.fail("old channel revived by takeover crash"))
    ctl.checkpoint = lambda stage: None
    token = ctl.current()
    if boundary == "RECORDED_UNCOMMITTED":
        assert token == old.token
        recovered = ctl.manual_takeover(token, receipt, "runtime", "p3", "explicit retry")
        assert recovered.token.epoch == 2
    else:
        assert token.epoch == 2
        fresh = authority.cut_off(token)
        ctl.manual_revoke(token, fresh, "manual recovery cutoff")
        recovered = ctl.manual_takeover(token, fresh, "runtime", "p3", "manual recovery")
        assert recovered.token.epoch == 3


def test_cutoff_of_committed_new_generation_cannot_be_undone_by_late_activation(execution_repo):
    repo, _ = execution_repo
    ctl, authority = control(repo)
    old = ctl.manual_initial("runtime", "p1", "manual startup")
    receipt = authority.cut_off(old.token)
    ctl.manual_revoke(old.token, receipt, "manual cutoff")
    captured = []

    def checkpoint(stage):
        if stage == "RECORD_COMMITTED":
            captured.append(authority.cut_off(ctl.current()))

    ctl.checkpoint = checkpoint
    with pytest.raises(PermissionError):
        ctl.manual_takeover(old.token, receipt, "runtime", "p2", "manual takeover")
    token = ctl.current()
    assert token.epoch == 2
    assert authority.active_token("a") is None
    ctl.checkpoint = lambda stage: None
    ctl.manual_revoke(token, captured[0], "new generation cut off before activation")
    new = ctl.manual_takeover(token, captured[0], "runtime", "p3", "manual recovery")
    assert new.token.epoch == 3
