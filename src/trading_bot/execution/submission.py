"""Immutable first-attempt clocks; acknowledgements do not restart them."""

from dataclasses import dataclass
from datetime import datetime

from trading_bot.domain.serialization import Record


@dataclass(frozen=True)
class SubmitWindow(Record):
    account_id: str
    intent_id: str
    started_at: datetime
    deadline: datetime


@dataclass(frozen=True)
class UnresolvedSubmit(Record):
    window: SubmitWindow
    overdue: bool
