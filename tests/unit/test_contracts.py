"""Catch float leakage, currency mixing, lossy serialization and ambiguous clocks."""

import importlib
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest


def api(module, name):
    # Until implemented, assert the missing contract explicitly (a red test, not import error).
    try:
        loaded = importlib.import_module(f"trading_bot.domain.{module}")
    except ModuleNotFoundError:
        pytest.fail(f"missing domain contract: {module}.{name}")
    assert hasattr(loaded, name), f"missing domain contract: {module}.{name}"
    return getattr(loaded, name)


def test_money_currency_mismatch_rejected():
    money = api("money", "Money")
    with pytest.raises(ValueError, match="currency"):
        _ = money(Decimal("12.4"), "SYNTH_Q") + money(Decimal("2"), "BTC")


def test_precision_round_trip():
    money = api("money", "Money")
    value = money(Decimal("12345678901234567890.0000000000000000001234500"), "SYNTH_Q")
    assert money.from_json(value.to_json()) == value
    assert "12345678901234567890.0000000000000000001234500" in value.to_json()


def test_addition_does_not_round_to_global_decimal_context():
    money = api("money", "Money")
    with localcontext() as context:
        context.prec = 5
        result = money(Decimal("123456789.00000001"), "SYNTH_Q") + money(
            Decimal("0.00000002"), "SYNTH_Q"
        )
    assert result.amount == Decimal("123456789.00000003")


@pytest.mark.parametrize("value", [0.1, "1.0", Decimal("NaN"), Decimal("Infinity")])
def test_money_rejects_float_string_and_nonfinite(value):
    money = api("money", "Money")
    with pytest.raises((TypeError, ValueError)):
        money(value, "SYNTH_Q")


def test_quantity_cannot_mix_base_or_money():
    quantity = api("money", "Quantity")
    money = api("money", "Money")
    with pytest.raises(ValueError, match="base"):
        _ = quantity(Decimal("1"), "BTC") + quantity(Decimal("2"), "ETH")
    with pytest.raises(TypeError):
        _ = quantity(Decimal("1"), "BTC") + money(Decimal("2"), "BTC")


def test_market_identity_includes_venue_and_asset_identity():
    instrument = api("records", "InstrumentId")
    first = instrument("crypto_com", "BTC_SUSD", "bitcoin:BTC", "synthetic:SUSD")
    other = instrument("other", "BTC_SUSD", "wrapped:BTC", "synthetic:SUSD")
    assert first != other
    assert instrument.from_json(first.to_json()) == first


def envelope(**changes):
    cls = api("events", "EventEnvelope")
    args = dict(
        event_id="event-1",
        run_id="run-offline",
        account_id=None,
        source="fake_public",
        source_seq="42",
        event_time=datetime(2026, 1, 1, tzinfo=UTC),
        received_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        available_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        schema_version=1,
        payload_ref="payloads/a.raw",
        payload_hash="a" * 64,
    )
    return cls(**(args | changes))


def test_envelope_round_trip_normalizes_utc_without_changing_instants():
    event = envelope(event_time=datetime(2026, 1, 1, 1, tzinfo=timezone(timedelta(hours=1))))
    encoded = event.to_json()
    assert "2026-01-01T00:00:00.000000Z" in encoded
    assert type(event).from_json(encoded) == event
    assert event.event_time.tzinfo is UTC


def test_envelope_requires_utc_and_availability_not_before_receipt():
    with pytest.raises(ValueError, match="aware"):
        envelope(received_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="available"):
        envelope(available_at=datetime(2025, 1, 1, tzinfo=UTC))


def test_envelope_rejects_unknown_schema_and_malformed_hash():
    with pytest.raises(ValueError, match="schema"):
        envelope(schema_version=2)
    with pytest.raises(ValueError, match="hash"):
        envelope(payload_hash="not-a-sha256")


def test_instrument_rules_validate_units_and_temporal_availability():
    rules = api("records", "InstrumentRules")
    instrument = api("records", "InstrumentId")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="positive"):
        rules(
            instrument("crypto_com", "BTC_SUSD", "bitcoin:BTC", "synthetic:SUSD"),
            "SPOT",
            "ACTIVE",
            Decimal("0"),
            Decimal("0.1"),
            Decimal("5"),
            now,
            now,
            1,
        )


@pytest.mark.parametrize("wire", ["0.1", "true", "null"])
def test_wire_decimal_requires_string(wire):
    money = api("money", "Money")
    with pytest.raises(TypeError, match="string"):
        money.from_json(
            '{"$type":"Money","$schema":1,"amount":{"$decimal":' + wire + '},"currency":"SYNTH_Q"}'
        )


def test_addition_ignores_ambient_exponent_limits():
    money = api("money", "Money")
    with localcontext() as context:
        context.Emax = 3
        result = money(Decimal("9999"), "SYNTH_Q") + money(Decimal("1"), "SYNTH_Q")
    assert result.amount == Decimal("10000")


def test_lot_identity_and_per_lot_deadlines_survive_wire_roundtrip():
    lot = api("records", "InventoryLot")
    deadline = api("records", "LotDeadline")
    state = api("records", "ProtectionState")
    instrument = api("records", "InstrumentId")(
        "crypto_com", "BTC_SUSD", "bitcoin:BTC", "synthetic:SUSD"
    )
    quantity = api("money", "Quantity")(Decimal("2"), "bitcoin:BTC")
    value = lot("lot-1", "fill-1", instrument, quantity, datetime(2026, 1, 1, tzinfo=UTC))
    assert type(value).from_json(value.to_json()).lot_id == "lot-1"
    first = deadline("lot-1", quantity, datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC))
    second = deadline("lot-2", quantity, datetime(2026, 1, 1, 0, 0, 6, tzinfo=UTC))
    protection = state(
        instrument,
        quantity,
        quantity,
        api("money", "Quantity")(Decimal("0"), "bitcoin:BTC"),
        (first, second),
        (),
        "CONFIRMED",
    )
    decoded = type(protection).from_json(protection.to_json())
    assert decoded.lot_deadlines[0].deadline == datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
    assert decoded.lot_deadlines[1].lot_id == "lot-2"


def test_approval_serializes_side_price_and_stop_bounds():
    approval = api("records", "RiskApproval")
    instrument = api("records", "InstrumentId")(
        "crypto_com", "BTC_SUSD", "bitcoin:BTC", "synthetic:SUSD"
    )
    money = api("money", "Money")
    value = approval(
        "approval-1",
        instrument,
        "BUY",
        api("money", "Quantity")(Decimal("1"), "bitcoin:BTC"),
        money(Decimal("100"), "synthetic:SUSD"),
        money(Decimal("1"), "synthetic:SUSD"),
        money(Decimal("99"), "synthetic:SUSD"),
        money(Decimal("98"), "synthetic:SUSD"),
        1,
        "a" * 64,
        datetime(2026, 1, 1, tzinfo=UTC),
        False,
    )
    decoded = type(value).from_json(value.to_json())
    assert decoded.price_bound.amount == Decimal("99")
    assert decoded.stop.amount == Decimal("98")
    assert decoded.side == "BUY"


def test_wire_record_schema_must_be_supported():
    import json

    money = api("money", "Money")
    wire = json.loads(money(Decimal("1"), "SYNTH_Q").to_json())
    wire["$schema"] = 2
    with pytest.raises(ValueError, match="schema"):
        money.from_json(json.dumps(wire))


def test_fill_rejects_mismatched_asset_and_price_currency():
    fill = api("records", "Fill")
    instrument = api("records", "InstrumentId")(
        "crypto_com", "BTC_SUSD", "bitcoin:BTC", "synthetic:SUSD"
    )
    with pytest.raises(ValueError, match="instrument"):
        fill(
            "fill-1",
            "fake-account",
            instrument,
            "order-1",
            "BUY",
            api("money", "Quantity")(Decimal("1"), "wrapped:BTC"),
            api("money", "Money")(Decimal("10"), "USD"),
            (),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
