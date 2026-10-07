"""Catch promotion of offline evidence/configuration into trading permission."""

import importlib
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_bot.domain.records import CapabilityEvidence


def api(module, name):
    try:
        loaded = importlib.import_module(f"trading_bot.config.{module}")
    except ModuleNotFoundError:
        pytest.fail(f"missing config contract: {module}.{name}")
    assert hasattr(loaded, name), f"missing config contract: {module}.{name}"
    return getattr(loaded, name)


def profiles():
    return (
        api("profiles", "ExecutionProfile")(),
        api("profiles", "RiskProfile")(),
        api("profiles", "ResearchProfile")(),
    )


def validate(execution, risk, research):
    return api("validation", "validate_profiles")(execution, risk, research)


def test_live_profile_incomplete_denied():
    execution, risk, research = profiles()
    report = validate(replace(execution, environment="live"), risk, research)
    assert report.status == "BLOCKED"
    assert "P01_LIVE_UNAVAILABLE" in report.reasons
    assert "QUOTE_PROFILE_INCOMPLETE" in report.reasons
    assert "CAPABILITY_UNKNOWN:V01" in report.reasons


def test_fake_pass_evidence_cannot_enable_live():
    execution, risk, research = profiles()
    evidence = tuple(
        CapabilityEvidence(
            f"V{i:02}", "live:fake", "PASS", "a" * 64, datetime(2026, 1, 1, tzinfo=UTC)
        )
        for i in range(1, 12)
    )
    report = validate(
        replace(
            execution,
            environment="live",
            evidence=evidence,
            quote_profile_complete=True,
            runtime_status="COMPLETE",
        ),
        risk,
        research,
    )
    assert report.status == "BLOCKED"
    assert "P01_LIVE_UNAVAILABLE" in report.reasons


def test_paper_rejects_production_trade_transport():
    execution, risk, research = profiles()
    report = validate(
        replace(execution, environment="paper", trade_transport="production"), risk, research
    )
    assert report.status == "BLOCKED"
    assert "TRADE_TRANSPORT_DISABLED" in report.reasons


@pytest.mark.parametrize("environment", ["research", "paper", "uat"])
def test_profiles_with_any_credentials_are_denied(environment):
    execution, risk, research = profiles()
    report = validate(
        replace(execution, environment=environment, credential_scope="read_only"), risk, research
    )
    assert report.status == "BLOCKED"
    assert "CREDENTIALS_FORBIDDEN_IN_P01" in report.reasons


@pytest.mark.parametrize(
    "name",
    [
        "caution_drawdown",
        "defensive_drawdown",
        "hard_pause_drawdown",
        "risk_per_entry",
        "max_positions",
        "submit_deadline",
    ],
)
def test_risk_thresholds_not_search_parameters(name):
    execution, risk, research = profiles()
    report = validate(execution, risk, replace(research, search_parameters=((name, "0.01"),)))
    assert report.status == "FAIL"
    assert f"SEARCH_PARAMETER_FORBIDDEN:{name}" in report.reasons


def test_approved_risk_threshold_cannot_be_overridden_or_mutated():
    execution, risk, research = profiles()
    with pytest.raises(FrozenInstanceError):
        risk.hard_pause_drawdown = Decimal("0.20")
    report = validate(execution, replace(risk, hard_pause_drawdown=Decimal("0.20")), research)
    assert report.status == "FAIL"
    assert "APPROVED_RISK_LIMITS_CHANGED" in report.reasons


def test_unsafe_endpoint_is_not_accepted_even_with_safe_transport_label():
    execution, risk, research = profiles()
    report = validate(
        replace(
            execution, public_endpoints=("https://api.crypto.com/exchange/v1/private/create-order",)
        ),
        risk,
        research,
    )
    assert report.status == "BLOCKED"
    assert "PUBLIC_ENDPOINT_NOT_ALLOWED" in report.reasons


def test_offline_profile_hash_is_stable_and_changes_with_every_section():
    execution, risk, research = profiles()
    assert validate(execution, risk, research).status == "PASS"
    assert execution.config_hash == type(execution).from_json(execution.to_json()).config_hash
    assert execution.config_hash != replace(execution, account_id="another-offline").config_hash
    assert risk.config_hash != replace(risk, max_positions=1).config_hash
    assert research.config_hash != replace(research, dataset_id="different").config_hash


@pytest.mark.parametrize("mode", ["research", "paper", "uat"])
def test_sanitized_profile_loads_with_explicit_synthetic_q(mode):
    bundle = api("profiles", "load_profiles")(Path("configs") / f"{mode}.toml")
    assert bundle.execution.environment == mode
    assert bundle.execution.quote_currency == "synthetic:SYNTH_Q"
    assert bundle.execution.credential_scope == "none"
    assert bundle.execution.trade_transport == "none"
    assert validate(bundle.execution, bundle.risk, bundle.research).status == "PASS"


@pytest.mark.parametrize(
    "extra", ['api_key = "fake"', "enable_live = true", 'unknown_field = "value"']
)
def test_config_rejects_secret_or_unknown_fields(tmp_path, extra):
    loader = api("profiles", "load_profiles")
    path = tmp_path / "bad.toml"
    path.write_text("[execution]\n" + extra + "\n")
    with pytest.raises(ValueError):
        loader(path)


def test_config_rejects_numeric_financial_decimal(tmp_path):
    loader = api("profiles", "load_profiles")
    path = tmp_path / "bad.toml"
    path.write_text("[risk]\nrisk_per_entry = 0.01\n")
    with pytest.raises(TypeError, match="string"):
        loader(path)
