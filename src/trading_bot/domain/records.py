"""Version-one foundational records. These DTOs do not implement their owning engines."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import NewType, Protocol

from .events import EventEnvelope
from .money import Money, Quantity
from .serialization import Record

SetupId = NewType("SetupId", str)
IntentId = NewType("IntentId", str)
RunId = NewType("RunId", str)
Cursor = NewType("Cursor", str)


@dataclass(frozen=True)
class InstrumentId(Record):
    venue: str
    market: str
    base: str
    quote: str

    def __post_init__(self):
        super().__post_init__()
        if any(
            not value or value != value.strip()
            for value in (self.venue, self.market, self.base, self.quote)
        ):
            raise ValueError("canonical venue, market and asset identities required")
        if self.base == self.quote:
            raise ValueError("base and quote must differ")


@dataclass(frozen=True)
class InstrumentRules(Record):
    instrument: InstrumentId
    instrument_type: str
    status: str
    tick_size: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    effective_at: datetime
    available_at: datetime
    version: int

    def __post_init__(self):
        super().__post_init__()
        if min(self.tick_size, self.min_quantity, self.max_quantity) <= 0:
            raise ValueError("tick and quantity limits must be positive")
        if self.max_quantity < self.min_quantity or self.version < 1:
            raise ValueError("invalid limits or version")


@dataclass(frozen=True)
class CapabilityEvidence(Record):
    capability_id: str
    scope: str
    status: str
    artifact_hash: str | None
    observed_at: datetime

    def __post_init__(self):
        super().__post_init__()
        if self.status not in {"PASS", "FAIL", "UNKNOWN", "DISABLED_VERIFIED"}:
            raise ValueError("invalid capability status")


@dataclass(frozen=True)
class GateReport(Record):
    gate_id: str
    status: str
    reasons: tuple[str, ...]

    def __post_init__(self):
        super().__post_init__()
        if self.status not in {"PASS", "BLOCKED", "FAIL", "INCONCLUSIVE"}:
            raise ValueError("invalid gate status")
        if self.status != "PASS" and not self.reasons:
            raise ValueError("non-PASS gate requires reasons")


@dataclass(frozen=True)
class Fee(Record):
    source_id: str
    account_id: str
    instrument: InstrumentId
    fill_id: str
    value: Money
    revision: int


@dataclass(frozen=True)
class Fill(Record):
    SCHEMA_VERSION = 2
    READABLE_SCHEMAS = (1, 2)
    source_id: str
    account_id: str
    instrument: InstrumentId
    order_id: str
    side: str
    quantity: Quantity
    price: Money
    fees: tuple[Fee, ...]
    event_time: datetime
    trade_id: str | None = None
    fee_final: bool = False

    def __post_init__(self):
        super().__post_init__()
        if (
            self.quantity.base != self.instrument.base
            or self.price.currency != self.instrument.quote
        ):
            raise ValueError("fill quantity/price must use instrument asset identities")


@dataclass(frozen=True)
class BalanceObservation(Record):
    source_id: str
    account_id: str
    total: Money
    available: Money
    observed_at: datetime


@dataclass(frozen=True)
class OrderObservation(Record):
    source_id: str
    account_id: str
    instrument: InstrumentId
    order_id: str
    cumulative_quantity: Quantity
    status: str
    terminal: bool
    observed_at: datetime


@dataclass(frozen=True)
class Reservation(Record):
    SCHEMA_VERSION = 2
    READABLE_SCHEMAS = (1, 2)
    reservation_id: str
    intent_id: IntentId
    cash: Money
    risk: Money
    ledger_version: int
    expires_at: datetime
    status: str
    account_id: str | None = None
    order_id: str | None = None
    instrument: InstrumentId | None = None
    side: str | None = None
    quantity: Quantity | None = None
    fee_buffers: tuple[Money, ...] = ()


@dataclass(frozen=True)
class InventoryLot(Record):
    SCHEMA_VERSION = 2
    READABLE_SCHEMAS = (1, 2)
    lot_id: str
    fill_id: str
    instrument: InstrumentId
    quantity: Quantity
    acquired_at: datetime
    cost: Money | None = None
    fees: tuple[Fee, ...] = ()
    risk: Money | None = None
    dust: bool = False
    origin_instrument: InstrumentId | None = None


@dataclass(frozen=True)
class LedgerSnapshot(Record):
    version: int
    balances: tuple[Money, ...]
    inventory: tuple[InventoryLot, ...]
    fee_commitments: tuple[Money, ...]
    reservations: tuple[Reservation, ...]
    nav: Money
    units: Decimal
    high_water_mark: Money
    uncertainty_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RiskApproval(Record):
    approval_id: str
    instrument: InstrumentId
    side: str
    max_quantity: Quantity
    max_cash: Money
    risk: Money
    price_bound: Money
    stop: Money
    ledger_version: int
    profile_hash: str
    expires_at: datetime
    consumed: bool


@dataclass(frozen=True)
class OrderIntent(Record):
    intent_id: IntentId
    account_id: str
    instrument: InstrumentId
    side: str
    quantity: Quantity
    price_bound: Money
    payload_hash: str
    approval_id: str
    reservation_id: str
    status: str


@dataclass(frozen=True)
class ExecutionEvent(Record):
    intent_id: IntentId
    kind: str
    envelope: EventEnvelope


@dataclass(frozen=True)
class ExecutionState(Record):
    intent_id: IntentId
    status: str
    observations: tuple[OrderObservation, ...]


@dataclass(frozen=True)
class CommandProposal(Record):
    command_id: str
    intent_id: IntentId
    kind: str
    payload_hash: str


@dataclass(frozen=True)
class LotDeadline(Record):
    lot_id: str
    quantity: Quantity
    deadline: datetime


@dataclass(frozen=True)
class ProtectionState(Record):
    instrument: InstrumentId
    target: Quantity
    covered: Quantity
    uncovered: Quantity
    lot_deadlines: tuple[LotDeadline, ...]
    competing_order_ids: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class ExitRequest(Record):
    request_id: str
    instrument: InstrumentId
    quantity: Quantity
    reason: str
    scope: str
    deadline: datetime


@dataclass(frozen=True)
class MarketSnapshot(Record):
    instrument: InstrumentId
    as_of: datetime
    available_at: datetime
    reference_price: Money
    bids: tuple[tuple[Money, Quantity], ...]
    asks: tuple[tuple[Money, Quantity], ...]
    metadata_version: int
    uncertainty_reasons: tuple[str, ...]


@dataclass(frozen=True)
class CostEstimate(Record):
    SCHEMA_VERSION = 2
    READABLE_SCHEMAS = (1, 2)
    samples: tuple[Money, ...]
    sizing_percentile: Decimal
    sizing_cost: Money
    source: str
    uncertainty_reasons: tuple[str, ...]
    basis: str = "TRIGGER_SHORTFALL"


@dataclass(frozen=True)
class RiskDecision(Record):
    setup_id: SetupId
    status: str
    constraints: tuple[str, ...]
    snapshot_version: int


@dataclass(frozen=True)
class RiskState(Record):
    mode: str
    latched_reasons: tuple[str, ...]
    deadlines: tuple[datetime, ...]
    version: int


@dataclass(frozen=True)
class HealthState(Record):
    status: str
    reasons: tuple[str, ...]
    observed_at: datetime


@dataclass(frozen=True)
class RecoveryReport(Record):
    status: str
    unresolved_evidence: tuple[str, ...]
    entry_permission: bool


@dataclass(frozen=True)
class CandleRevision(Record):
    instrument: InstrumentId
    start: datetime
    end: datetime
    available_at: datetime
    revision: int
    open: Money
    high: Money
    low: Money
    close: Money
    volume: Quantity
    coverage_reasons: tuple[str, ...]

    def __post_init__(self):
        super().__post_init__()
        if self.end <= self.start:
            raise ValueError("candle interval must be nonempty and half-open")


@dataclass(frozen=True)
class DatasetManifest(Record):
    dataset_id: str
    schema_version: int
    source_hashes: tuple[str, ...]
    lineage: tuple[str, ...]
    coverage_reasons: tuple[str, ...]


@dataclass(frozen=True)
class EligibilityDecision(Record):
    instrument: InstrumentId
    as_of: datetime
    eligible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class FeatureSnapshot(Record):
    instrument: InstrumentId
    as_of: datetime
    h: Decimal
    low: Decimal
    a: Decimal
    s: Decimal
    activity: Decimal
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class Setup(Record):
    setup_id: SetupId
    instrument: InstrumentId
    variant: str
    expires_at: datetime
    episode_id: str
    feature_hash: str


@dataclass(frozen=True)
class StrategyState(Record):
    setup_id: SetupId
    retest_status: str
    episode_id: str
    version: int


@dataclass(frozen=True)
class RunManifest(Record):
    run_id: RunId
    schema_version: int
    profile_hashes: tuple[str, ...]
    dataset_hashes: tuple[str, ...]
    code_version: str


@dataclass(frozen=True)
class SimulationResult(Record):
    run: RunManifest
    events: tuple[EventEnvelope, ...]
    ledger: tuple[LedgerSnapshot, ...]
    decisions: tuple[RiskDecision, ...]
    uncertainty_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentReport(Record):
    run: RunManifest
    results_hash: str
    gates: tuple[GateReport, ...]
    uncertainty_reasons: tuple[str, ...]


class ExchangePort(Protocol):
    """Interface only. P01 supplies no implementation or trade transport."""

    def submit(self, intent: OrderIntent) -> tuple[OrderObservation, ...]: ...
    def cancel(self, intent: OrderIntent) -> tuple[OrderObservation, ...]: ...
    def observe_orders(self, scope: str) -> tuple[OrderObservation, ...]: ...
    def observe_balances(self) -> tuple[BalanceObservation, ...]: ...
    def read_fills(self, cursor: Cursor | None) -> tuple[Fill, ...]: ...
    def subscribe_private(self) -> tuple[EventEnvelope, ...]: ...


class MarketDataPort(Protocol):
    def subscribe(self, scope: str) -> tuple[EventEnvelope, ...]: ...
    def snapshot(self, instrument: InstrumentId) -> MarketSnapshot: ...


@dataclass(frozen=True)
class FundingEvidence(Record):
    evidence_id: str
    account_id: str
    ledger_version: int
    paused: bool
    reconciled: bool
    observed_at: datetime
