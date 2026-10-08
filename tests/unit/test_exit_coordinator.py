from dataclasses import replace
from datetime import timedelta

import pytest
from accounting_helpers import BTC, NOW, D
from execution_helpers import execution_module
from test_protection import context

from trading_bot.domain.money import Quantity
from trading_bot.domain.records import ExitRequest


def exit_state(policy="SERIAL_CANCEL_THEN_MARKET_VERIFIED", **changes):
    e = execution_module("execution.exits")
    verified = e.SyntheticExitVerification("a", BTC, policy, "synthetic:fixture", True)
    return replace(
        e.ExitState(
            "a",
            context(
                stop_id="stop1", stop_status="CONFIRMED", stop_quantity=Quantity(D("4"), "BTC")
            ),
            Quantity(D("4"), "BTC"),
            policy,
            verified,
        ),
        **changes,
    )


def request():
    return ExitRequest(
        "exit1", BTC, Quantity(D("4"), "BTC"), "time", "a", NOW + timedelta(seconds=5)
    )


def test_cancel_ack_is_not_sell_permission():
    e = execution_module("execution.exits")
    state = exit_state(stop_status="CANCEL_PENDING")
    assert e.request_exit(request(), state) == []


def test_terminal_cancel_requires_actual_fills_reconciliation():
    e = execution_module("execution.exits")
    state = exit_state(
        stop_status="CANCELED",
        cumulative_filled=Quantity(D("1"), "BTC"),
        actual_filled=Quantity(D("0"), "BTC"),
        reconciled=True,
    )
    assert e.request_exit(request(), state) == []


def test_stop_fill_during_soft_exit_prevents_oversell():
    e = execution_module("execution.exits")
    state = exit_state(
        stop_status="CANCELED",
        remaining=Quantity(D("3"), "BTC"),
        cumulative_filled=Quantity(D("1"), "BTC"),
        actual_filled=Quantity(D("1"), "BTC"),
        reconciled=True,
    )
    commands = e.request_exit(request(), state)
    assert [(c.kind, c.quantity.amount) for c in commands] == [("MARKET_SELL", D("3"))]


@pytest.mark.parametrize("policy", ["SERIAL_CANCEL_THEN_MARKET_VERIFIED", "NATIVE_LINKED_VERIFIED"])
def test_each_exit_policy_requires_own_scoped_synthetic_verification(policy):
    e = execution_module("execution.exits")
    state = exit_state(policy)
    assert e.request_exit(request(), replace(state, verification=None)) == []
    assert (
        e.request_exit(
            request(), replace(state, verification=replace(state.verification, account_id="other"))
        )
        == []
    )
    expected = "CANCEL_STOP" if policy.startswith("SERIAL") else "NATIVE_LINKED_EXIT"
    assert [c.kind for c in e.request_exit(request(), state)] == [expected]


@pytest.mark.parametrize(
    "change", [{"stop_status": "UNKNOWN"}, {"sell_status": "UNKNOWN"}, {"sell_status": "PENDING"}]
)
def test_unknown_previous_stop_or_sell_never_duplicates_sell(change):
    e = execution_module("execution.exits")
    assert e.request_exit(request(), exit_state(**change)) == []


def test_native_policy_does_not_fallback_to_serial_market_sell():
    e = execution_module("execution.exits")
    state = exit_state(
        "NATIVE_LINKED_VERIFIED",
        stop_status="CANCELED",
        reconciled=True,
        cumulative_filled=Quantity(D("0"), "BTC"),
        actual_filled=Quantity(D("0"), "BTC"),
    )
    assert e.request_exit(request(), state) == []


@pytest.mark.parametrize("request_qty,remaining", [("3", "4"), ("4", "5")])
def test_native_linked_requires_full_matching_stop_right(request_qty, remaining):
    e = execution_module("execution.exits")
    state = exit_state("NATIVE_LINKED_VERIFIED", remaining=Quantity(D(remaining), "BTC"))
    assert e.request_exit(replace(request(), quantity=Quantity(D(request_qty), "BTC")), state) == []
