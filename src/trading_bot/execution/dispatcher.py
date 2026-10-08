"""Explicit synthetic transport only. No credentials, endpoints or automatic retry."""

from datetime import UTC, datetime

from trading_bot.execution.ownership import require_writer
from trading_bot.execution.protection import ProtectionClock
from trading_bot.operations.clock import RealClock
from trading_bot.storage.execution_repository import ExecutionRepository


class FakeExchange:
    def __init__(self, observations=(), *, error=None, writer=None):
        self.observations = observations
        self.error = error
        self.writer = writer
        self.submissions = []

    def submit(self, intent, *, repository=None, now=None):
        require_writer(self.writer)
        if (
            type(repository) is not ExecutionRepository
            or now is None
            or repository.account_id != intent.account_id
        ):
            raise PermissionError("durable send admission required")
        return self.writer.send(
            intent.account_id, lambda: repository._transmit(intent, now, self._submit)
        )

    def _submit(self, intent):
        self.submissions.append(intent)
        if self.error is not None:
            raise self.error
        return self.observations


class FakeDispatcher:
    def __init__(self, repository, exchange, *, checkpoint=None, clock=None):
        if type(exchange) is not FakeExchange:
            raise TypeError("fake-only transport required; live remains BLOCKED")
        self.repository, self.exchange = repository, exchange
        self.checkpoint = checkpoint or (lambda stage: None)
        self.clock = clock or (lambda: datetime.now(UTC))

    def dispatch(self, intent_id):
        require_writer(self.exchange.writer)
        self.exchange.writer.check(self.repository.account_id)
        self.checkpoint("PREPARED")
        if not self.repository.claim(intent_id, now=self.clock()):
            return False
        self.checkpoint("DISPATCHING")
        intent = self.repository.get_intent(intent_id)
        try:
            observations = self.exchange.submit(
                intent, repository=self.repository, now=self.clock()
            )
            self.checkpoint("SEND")
            if not observations:
                self.repository.mark_unknown(intent_id)
                return True
            for observation in observations:
                self.repository.apply_event(
                    intent_id,
                    observation,
                    protection_clock=ProtectionClock(RealClock(wall_now=self.clock)),
                )
            self.checkpoint("ACK")
        except TimeoutError:
            self.repository.mark_unknown(intent_id)
            return True
        except Exception:
            self.repository.mark_unknown(intent_id)
            raise
        return True
