"""Local synthetic exchange only. No transport, credential, endpoint or live V claim."""


class FakeLifecycleExchange:
    def execute(self, repository, command_id, clock):
        return repository.execute_fake(command_id, clock)
