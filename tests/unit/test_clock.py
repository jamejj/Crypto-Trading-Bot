"""Catch wall-clock extensions, nondeterministic equal deadlines and timer restart reset."""

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest


def api(name):
    try:
        module = importlib.import_module("trading_bot.operations.clock")
    except ModuleNotFoundError:
        pytest.fail(f"missing clock contract: {name}")
    assert hasattr(module, name), f"missing clock contract: {name}"
    return getattr(module, name)


def test_utc_jump_does_not_extend_monotonic_deadline():
    clock = api("SimulationClock")(datetime(2026, 1, 1, tzinfo=UTC))
    clock.schedule(datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC), "protection-expired")
    clock.advance(Decimal("2"))
    clock.jump_utc(datetime(2025, 12, 31, tzinfo=UTC))
    clock.advance(Decimal("3"))
    assert clock.pop_due() == ("protection-expired",)
    assert clock.pop_due() == ()


def replay():
    clock = api("SimulationClock")(datetime(2026, 1, 1, tzinfo=UTC))
    deadline = clock.utc_now() + timedelta(seconds=5)
    clock.schedule(deadline, "first")
    clock.schedule(deadline, "second")
    clock.schedule(deadline + timedelta(seconds=1), "third")
    clock.advance(Decimal("4.999999"))
    assert clock.pop_due() == ()
    clock.advance(Decimal("0.000001"))
    first = clock.pop_due()
    clock.advance(Decimal("1"))
    return first, clock.pop_due(), clock.utc_now(), clock.monotonic_now()


def test_deterministic_timer_replay():
    assert replay() == replay()
    assert replay() == (
        ("first", "second"),
        ("third",),
        datetime(2026, 1, 1, 0, 0, 6, tzinfo=UTC),
        Decimal("6"),
    )


def test_elapsed_persistent_deadline_is_immediately_due_after_restart():
    clock = api("SimulationClock")(datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC))
    clock.schedule(datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC), "expired-before-restart")
    assert clock.pop_due() == ("expired-before-restart",)


def test_real_clock_uses_monotonic_ns_and_normalizes_utc():
    wall = [datetime(2026, 1, 1, tzinfo=UTC)]
    monotonic = [10_000_000_000]
    clock = api("RealClock")(wall_now=lambda: wall[0], monotonic_ns=lambda: monotonic[0])
    clock.schedule(wall[0] + timedelta(seconds=5), "expired")
    wall[0] -= timedelta(days=1)
    monotonic[0] += 5_000_000_000
    assert clock.pop_due() == ("expired",)
    assert clock.monotonic_now() == Decimal("15")


@pytest.mark.parametrize("value", [Decimal("-1"), Decimal("NaN"), 0.1])
def test_simulation_rejects_invalid_monotonic_advance(value):
    clock = api("SimulationClock")(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises((ValueError, TypeError)):
        clock.advance(value)


def test_schedule_rejects_naive_deadline():
    clock = api("SimulationClock")(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="aware"):
        clock.schedule(datetime(2026, 1, 1), "invalid")


def test_replay_and_real_clock_ignore_ambient_decimal_precision():
    from decimal import localcontext

    with localcontext() as context:
        context.prec = 5
        assert replay() == (
            ("first", "second"),
            ("third",),
            datetime(2026, 1, 1, 0, 0, 6, tzinfo=UTC),
            Decimal("6"),
        )
        clock = api("RealClock")(
            wall_now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
            monotonic_ns=lambda: 1234567890123456789,
        )
        assert clock.monotonic_now() == Decimal("1234567890.123456789")
