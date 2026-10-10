import pytest
from accounting_helpers import NOW  # noqa: F401
from execution_helpers import execution_module, observation, prepare, writer


@pytest.mark.parametrize(
    "boundary, submits, status",
    [
        ("PREPARED", 0, "PREPARED"),
        ("DISPATCHING", 0, "SUBMISSION_UNKNOWN"),
        ("SEND", 1, "SUBMISSION_UNKNOWN"),
        ("ACK", 1, "RESOLVED"),
    ],
)
def test_restart_never_resends_ambiguous_create(execution_repo, boundary, submits, status):
    repo, _ = execution_repo
    intent = prepare(repo)

    class Crash(BaseException):
        pass

    def checkpoint(stage):
        if stage == boundary:
            raise Crash()

    module = execution_module("execution.dispatcher")
    exchange = module.FakeExchange((observation(),), writer=writer(repo))
    dispatcher = module.FakeDispatcher(repo, exchange, checkpoint=checkpoint, clock=lambda: NOW)
    with pytest.raises(Crash):
        dispatcher.dispatch(intent.intent_id)
    restarted = type(repo)(repo.connect, "a", "Q")
    restarted.recover_ambiguous()
    fresh = module.FakeDispatcher(restarted, exchange, clock=lambda: NOW)
    assert restarted.get_intent(intent.intent_id).status == status
    assert restarted.ledger.snapshot.reservations[0].status == "PENDING"
    if status != "PREPARED":
        assert fresh.dispatch(intent.intent_id) is False
    assert len(exchange.submissions) == submits
    from datetime import timedelta

    window = restarted.submit_window(intent.intent_id)
    if boundary == "PREPARED":
        assert window is None
    else:
        assert window.started_at == NOW and window.deadline == NOW + timedelta(seconds=5)


def test_transport_timeout_retains_reservation_without_retry(execution_repo):
    repo, _ = execution_repo
    intent = prepare(repo)
    module = execution_module("execution.dispatcher")
    exchange = module.FakeExchange(error=TimeoutError("ambiguous"), writer=writer(repo))
    dispatcher = module.FakeDispatcher(repo, exchange, clock=lambda: NOW)
    dispatcher.dispatch(intent.intent_id)
    assert repo.get_intent(intent.intent_id).status == "SUBMISSION_UNKNOWN"
    assert dispatcher.dispatch(intent.intent_id) is False
    assert len(exchange.submissions) == 1
    assert repo.ledger.snapshot.reservations[0].cash.amount == 40


def test_expired_prepared_intent_is_aborted_without_transmission(execution_repo):
    from datetime import timedelta

    repo, _ = execution_repo
    intent = prepare(repo)
    module = execution_module("execution.dispatcher")
    exchange = module.FakeExchange((observation(),), writer=writer(repo))
    dispatcher = module.FakeDispatcher(repo, exchange, clock=lambda: NOW + timedelta(minutes=2))
    assert dispatcher.dispatch(intent.intent_id) is False
    assert repo.get_intent(intent.intent_id).status == "ABORTED_BEFORE_SEND"
    assert exchange.submissions == []


@pytest.mark.parametrize("kind", ["fill", "ack"])
def test_prepared_with_existing_order_evidence_never_submits(execution_repo, kind):
    from accounting_helpers import fill

    repo, _ = execution_repo
    intent = prepare(repo)
    repo.apply_event(intent.intent_id, fill() if kind == "fill" else observation())
    module = execution_module("execution.dispatcher")
    exchange = module.FakeExchange((observation(),), writer=writer(repo))
    dispatcher = module.FakeDispatcher(repo, exchange, clock=lambda: NOW)
    assert dispatcher.dispatch(intent.intent_id) is False
    assert exchange.submissions == []
    assert repo.get_intent(intent.intent_id).status == "RESOLVED"
    assert repo.ledger.snapshot.reservations[0].status == "PENDING"


@pytest.mark.parametrize("claimed", [False, True])
def test_public_submit_without_durable_send_admission_is_forbidden(execution_repo, claimed):
    from execution_helpers import writer

    repo, _ = execution_repo
    intent = prepare(repo)
    if claimed:
        repo.claim(intent.intent_id, now=NOW)
    module = execution_module("execution.dispatcher")
    exchange = module.FakeExchange((observation(),), writer=writer(repo))
    with pytest.raises(PermissionError):
        exchange.submit(intent)
    assert exchange.submissions == []


def test_admitted_public_submit_is_one_shot_even_without_dispatcher(execution_repo):
    repo, _ = execution_repo
    intent = prepare(repo)
    repo.claim(intent.intent_id, now=NOW)
    module = execution_module("execution.dispatcher")
    exchange = module.FakeExchange(writer=writer(repo))
    exchange.submit(repo.get_intent(intent.intent_id), repository=repo, clock=lambda: NOW)
    with pytest.raises(PermissionError, match="one-shot"):
        exchange.submit(repo.get_intent(intent.intent_id), repository=repo, clock=lambda: NOW)
    assert len(exchange.submissions) == 1
    assert repo.get_intent(intent.intent_id).status == "SUBMISSION_UNKNOWN"


def test_public_submit_rejects_counterfeit_admission_repository(execution_repo):
    repo, _ = execution_repo
    intent = prepare(repo)
    module = execution_module("execution.dispatcher")
    exchange = module.FakeExchange(writer=writer(repo))

    class Counterfeit:
        account_id = "a"

        def _transmit(self, item, now, callback):
            return callback(item)

    with pytest.raises(PermissionError, match="durable"):
        exchange.submit(intent, repository=Counterfeit(), clock=lambda: NOW)
    assert exchange.submissions == []


def test_owned_send_without_configured_protection_is_forbidden(execution_repo):
    repo, _ = execution_repo
    intent = prepare(repo)
    repo.claim(intent.intent_id, now=NOW)
    ownership = execution_module("execution.ownership")
    control = ownership.OwnershipControl(repo.connect, "a", ownership.FakeFenceAuthority())
    guard = control.manual_initial("fixture", "process", "explicit fixture grant")
    exchange = execution_module("execution.dispatcher").FakeExchange(writer=guard)
    with pytest.raises(PermissionError, match="protection"):
        exchange.submit(repo.get_intent(intent.intent_id), repository=repo, clock=lambda: NOW)
    assert exchange.submissions == []


@pytest.mark.parametrize("missing_window", [False, True])
def test_send_needs_unexpired_durable_attempt_window(execution_repo, missing_window):
    from datetime import timedelta

    repo, connection = execution_repo
    intent = prepare(repo)
    exchange = execution_module("execution.dispatcher").FakeExchange(writer=writer(repo))
    if missing_window:
        with connection() as conn:
            conn.execute("UPDATE execution_outbox SET state='DISPATCHING'")
    else:
        repo.claim(intent.intent_id, now=NOW)
    with pytest.raises(PermissionError, match="deadline|window"):
        exchange.submit(
            repo.get_intent(intent.intent_id),
            repository=repo,
            clock=lambda: NOW + timedelta(seconds=6),
        )
    assert exchange.submissions == []
    assert repo.ledger.snapshot.reservations[0].cash.amount == 40
