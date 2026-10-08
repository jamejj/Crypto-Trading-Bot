"""Explicit synthetic transport only. No credentials, endpoints or automatic retry."""

from datetime import UTC, datetime


class FakeExchange:
    def __init__(self, observations=(), *, error=None):
        self.observations = observations
        self.error = error
        self.submissions = []

    def submit(self, intent):
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
        self.checkpoint("PREPARED")
        if not self.repository.claim(intent_id, now=self.clock()):
            return False
        self.checkpoint("DISPATCHING")
        intent = self.repository.get_intent(intent_id)
        try:
            observations = self.exchange.submit(intent)
            self.checkpoint("SEND")
            if not observations:
                self.repository.mark_unknown(intent_id)
                return True
            for observation in observations:
                self.repository.apply_event(intent_id, observation)
            self.checkpoint("ACK")
        except TimeoutError:
            self.repository.mark_unknown(intent_id)
            return True
        except Exception:
            self.repository.mark_unknown(intent_id)
            raise
        return True
