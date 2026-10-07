from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .serialization import Record


@dataclass(frozen=True)
class EventEnvelope(Record):
    event_id: str
    run_id: str
    account_id: str | None
    source: str
    source_seq: str | None
    event_time: datetime | None
    received_at: datetime
    available_at: datetime
    schema_version: int
    payload_ref: str
    payload_hash: str
    received_monotonic: Decimal = Decimal("0")

    def __post_init__(self):
        super().__post_init__()
        if self.schema_version != 1:
            raise ValueError("unsupported envelope schema version")
        if self.available_at < self.received_at:
            raise ValueError("available_at precedes received_at")
        if not all((self.event_id, self.run_id, self.source, self.payload_ref)):
            raise ValueError("envelope identity and payload reference required")
        if len(self.payload_hash) != 64 or any(
            c not in "0123456789abcdef" for c in self.payload_hash
        ):
            raise ValueError("payload hash must be lowercase SHA-256")
        if self.received_monotonic < 0:
            raise ValueError("monotonic offset must be nonnegative")
