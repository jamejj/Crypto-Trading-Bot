"""Currency-tagged exact values. Valuation/conversion belongs to later accounting stages."""

from dataclasses import dataclass
from decimal import Decimal

from .serialization import Record, exact_add


@dataclass(frozen=True)
class Money(Record):
    amount: Decimal
    currency: str

    def __post_init__(self):
        super().__post_init__()
        if not self.currency or self.currency != self.currency.strip():
            raise ValueError("currency identity required")

    def __add__(self, other):
        if type(other) is not Money:
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("currency mismatch")
        return Money(exact_add(self.amount, other.amount), self.currency)


@dataclass(frozen=True)
class Quantity(Record):
    amount: Decimal
    base: str

    def __post_init__(self):
        super().__post_init__()
        if not self.base or self.base != self.base.strip():
            raise ValueError("base identity required")
        if self.amount < 0:
            raise ValueError("quantity must be nonnegative")

    def __add__(self, other):
        if type(other) is not Quantity:
            return NotImplemented
        if self.base != other.base:
            raise ValueError("base mismatch")
        return Quantity(exact_add(self.amount, other.amount), self.base)
