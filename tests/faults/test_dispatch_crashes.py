import pytest
from accounting_helpers import NOW  # noqa: F401
from execution_helpers import execution_module, observation, prepare


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
    exchange = module.FakeExchange((observation(),))
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


def test_transport_timeout_retains_reservation_without_retry(execution_repo):
    repo, _ = execution_repo
    intent = prepare(repo)
    module = execution_module("execution.dispatcher")
    exchange = module.FakeExchange(error=TimeoutError("ambiguous"))
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
    exchange = module.FakeExchange((observation(),))
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
    exchange = module.FakeExchange((observation(),))
    dispatcher = module.FakeDispatcher(repo, exchange, clock=lambda: NOW)
    assert dispatcher.dispatch(intent.intent_id) is False
    assert exchange.submissions == []
    assert repo.get_intent(intent.intent_id).status == "RESOLVED"
    assert repo.ledger.snapshot.reservations[0].status == "PENDING"
