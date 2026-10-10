"""Positive fake-venue snapshot evidence; local NONE is never absence evidence."""

from dataclasses import dataclass
from datetime import datetime

from trading_bot.domain.money import Quantity
from trading_bot.domain.records import Fill, InstrumentId
from trading_bot.domain.serialization import Record


@dataclass(frozen=True)
class SyntheticVenueOrder(Record):
    account_id: str
    instrument: InstrumentId
    order_id: str
    side: str
    status: str


@dataclass(frozen=True)
class SyntheticVenueSnapshot(Record):
    source_id: str
    account_id: str
    base: str
    sequence: int
    observed_at: datetime
    orders_complete: bool
    trades_complete: bool
    orders: tuple[SyntheticVenueOrder, ...]
    trades: tuple[Fill, ...]


@dataclass(frozen=True)
class SyntheticAbsenceEvidence(Record):
    account_id: str
    instrument: InstrumentId
    ledger_version: int
    quantity: Quantity
    snapshot: SyntheticVenueSnapshot
