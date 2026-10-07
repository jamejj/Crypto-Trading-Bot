"""Explicit clocks with monotonic timers, integer nanoseconds, and no hidden I/O."""

import heapq
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from trading_bot.domain.events import EventEnvelope
from trading_bot.domain.serialization import decimal, utc

Event = str | EventEnvelope


def _nanoseconds(seconds: Decimal) -> int:
    decimal(seconds)
    value = seconds.as_tuple()
    coefficient = int("".join(str(digit) for digit in value.digits))
    scale = value.exponent + 9
    if scale >= 0:
        result = coefficient * 10**scale
    else:
        result, remainder = divmod(coefficient, 10**-scale)
        if remainder:
            raise ValueError("clock resolution is one nanosecond")
    return -result if value.sign else result


def _seconds(nanoseconds: int) -> Decimal:
    # Decimal tuple construction does not consult the current context.
    return Decimal((int(nanoseconds < 0), tuple(int(c) for c in str(abs(nanoseconds))), -9))


def _delta_nanoseconds(delta: timedelta) -> int:
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000


class Clock(Protocol):
    def utc_now(self) -> datetime: ...
    def monotonic_now(self) -> Decimal: ...
    def schedule(self, deadline: datetime, event: Event) -> None: ...
    def pop_due(self) -> tuple[Event, ...]: ...


class _TimerQueue:
    def __init__(self):
        self._timers: list[tuple[int, int, Event]] = []
        self._next_sequence = 0

    def schedule(self, deadline: datetime, event: Event) -> None:
        remaining = max(0, _delta_nanoseconds(utc(deadline) - self.utc_now()))
        due = _nanoseconds(self.monotonic_now()) + remaining
        heapq.heappush(self._timers, (due, self._next_sequence, event))
        self._next_sequence += 1

    def pop_due(self) -> tuple[Event, ...]:
        now = _nanoseconds(self.monotonic_now())
        due = []
        while self._timers and self._timers[0][0] <= now:
            due.append(heapq.heappop(self._timers)[2])
        return tuple(due)


class RealClock(_TimerQueue):
    def __init__(
        self,
        *,
        wall_now: Callable[[], datetime] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
    ):
        super().__init__()
        self._wall = wall_now if wall_now is not None else lambda: datetime.now(UTC)
        self._monotonic_ns = monotonic_ns if monotonic_ns is not None else time.monotonic_ns

    def utc_now(self) -> datetime:
        return utc(self._wall())

    def monotonic_now(self) -> Decimal:
        value = self._monotonic_ns()
        if type(value) is not int or value < 0:
            raise ValueError("nonnegative integer monotonic nanoseconds required")
        return _seconds(value)


class SimulationClock(_TimerQueue):
    def __init__(self, start: datetime):
        super().__init__()
        self._start = utc(start)
        self._elapsed_ns = 0
        self._wall_shift = timedelta()

    def utc_now(self) -> datetime:
        return self._start + timedelta(microseconds=self._elapsed_ns // 1000) + self._wall_shift

    def monotonic_now(self) -> Decimal:
        return _seconds(self._elapsed_ns)

    def advance(self, seconds: Decimal) -> None:
        increment = _nanoseconds(seconds)
        if increment < 0:
            raise ValueError("monotonic clock cannot run backwards")
        self._elapsed_ns += increment

    def jump_utc(self, new_wall_time: datetime) -> None:
        self._wall_shift += utc(new_wall_time) - self.utc_now()
