"""Explicit immutable profiles. No credential loading or live assembly exists in P01."""

import hashlib
import tomllib
from dataclasses import dataclass, fields
from decimal import Decimal
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

from trading_bot.domain.records import CapabilityEvidence, InstrumentId
from trading_bot.domain.serialization import Record

PUBLIC_INSTRUMENTS_URLS = (
    "https://api.crypto.com/exchange/v1/public/get-instruments",
    "https://uat-api.3ona.co/exchange/v1/public/get-instruments",
)


class Profile(Record):
    @property
    def config_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExecutionProfile(Profile):
    schema_version: int = 1
    account_id: str = "offline-synthetic"
    environment: str = "research"
    public_endpoints: tuple[str, ...] = ()
    quote_currency: str = "synthetic:SYNTH_Q"
    allowed_instruments: tuple[InstrumentId, ...] = ()
    allowed_types: tuple[str, ...] = ("SPOT",)
    order_type: str = "LIMIT"
    time_in_force: str = "FILL_OR_KILL"
    trigger_source: str = "UNVERIFIED"
    exit_policy: str = "DISABLED"
    retry_policy: str = "NEVER"
    fee_policy: str = "SYNTHETIC_OFFLINE"
    deadlines: tuple[tuple[str, Decimal], ...] = (
        ("book_freshness", Decimal("1")),
        ("entry_validity", Decimal("2")),
        ("submit_unresolved", Decimal("5")),
        ("protection_target", Decimal("2")),
        ("unprotected", Decimal("5")),
        ("reconciliation_period", Decimal("10")),
        ("reconciliation_pause", Decimal("30")),
    )
    evidence: tuple[CapabilityEvidence, ...] = ()
    credential_scope: str = "none"
    trade_transport: str = "none"
    quote_profile_complete: bool = False
    runtime_status: str = "INCOMPLETE"


@dataclass(frozen=True)
class RiskProfile(Profile):
    schema_version: int = 1
    caution_drawdown: Decimal = Decimal("0.06")
    defensive_drawdown: Decimal = Decimal("0.09")
    hard_pause_drawdown: Decimal = Decimal("0.12")
    risk_per_entry: Decimal = Decimal("0.01")
    aggregate_planned_risk: Decimal = Decimal("0.02")
    max_positions: int = 2
    max_asset_exposure: Decimal = Decimal("0.50")
    max_total_exposure: Decimal = Decimal("0.90")
    normal_multiplier: Decimal = Decimal("1.00")
    caution_multiplier: Decimal = Decimal("0.70")
    defensive_multiplier: Decimal = Decimal("0.40")


@dataclass(frozen=True)
class ResearchProfile(Profile):
    schema_version: int = 1
    baseline: str = "R0"
    timeframe_seconds: int = 300
    time_exit_boundaries: int = 24
    variants: tuple[str, ...] = ("A", "B")
    dataset_id: str = "synthetic-offline"
    split_manifest_hash: str = "UNFROZEN"
    metric_manifest_hash: str = "UNFROZEN"
    # Only hypothesis parameters may become search parameters. Approved safety limits cannot.
    search_parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProfileBundle(Record):
    execution: ExecutionProfile
    risk: RiskProfile
    research: ResearchProfile


def _toml_value(value, annotation):
    if annotation is Decimal:
        if not isinstance(value, str):
            raise TypeError("TOML Decimal requires a string")
        return Decimal(value)
    if get_origin(annotation) is tuple:
        if not isinstance(value, list):
            raise TypeError("TOML tuple requires an array")
        args = get_args(annotation)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_toml_value(item, args[0]) for item in value)
        if len(args) != len(value):
            raise ValueError("tuple arity mismatch")
        return tuple(_toml_value(item, kind) for item, kind in zip(value, args, strict=True))
    return value


def load_profiles(path: Path) -> ProfileBundle:
    """Read only the named TOML. Unknown fields fail closed; no environment/secret lookup."""
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    classes = {"execution": ExecutionProfile, "risk": RiskProfile, "research": ResearchProfile}
    if set(data) - classes.keys():
        raise ValueError("unknown profile section")
    profiles = {}
    for name, kind in classes.items():
        section = data.get(name, {})
        if not isinstance(section, dict) or set(section) - {field.name for field in fields(kind)}:
            raise ValueError(f"unknown or malformed {name} configuration")
        hints = get_type_hints(kind)
        profiles[name] = kind(
            **{key: _toml_value(value, hints[key]) for key, value in section.items()}
        )
    return ProfileBundle(**profiles)
