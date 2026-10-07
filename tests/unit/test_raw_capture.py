"""Exercise real filesystem capture; fake only public network boundary."""

import hashlib
import importlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_bot.domain.events import EventEnvelope
from trading_bot.operations.clock import SimulationClock


def api(name):
    try:
        module = importlib.import_module("trading_bot.data.raw_capture")
    except ModuleNotFoundError:
        pytest.fail(f"missing capture contract: {name}")
    assert hasattr(module, name), f"missing capture contract: {name}"
    return getattr(module, name)


def capture(tmp_path, **kwargs):
    clock = SimulationClock(datetime(2026, 1, 1, tzinfo=UTC))
    return api("RawCapture")(tmp_path / "session", clock, "run-offline", **kwargs), clock


def rows(store):
    return [json.loads(line) for line in (store.root / "events.jsonl").read_text().splitlines()]


def test_original_payload_and_receipt_are_preserved_exactly(tmp_path):
    store, clock = capture(tmp_path)
    clock.advance(Decimal("1.25"))
    original = b'{ "code": 0, "value": 0.1234567890123456789012345 }\n'
    event = store.receive("fake_public", original, source_seq="1")
    assert (store.root / event.payload_ref).read_bytes() == original
    assert event.payload_hash == hashlib.sha256(original).hexdigest()
    assert event.received_at == datetime(2026, 1, 1, 0, 0, 1, 250000, tzinfo=UTC)
    assert event.received_monotonic == Decimal("1.25")
    assert event.event_time is None
    assert EventEnvelope.from_json(json.dumps(rows(store)[0]["envelope"])) == event


def test_fake_public_feed_preserves_duplicate_gap_and_reconnect(tmp_path):
    store, clock = capture(tmp_path)
    store.receive("fake_public", b'{"seq":1}', source_seq="1")
    store.receive("fake_public", b'{"seq":1}', source_seq="1")
    clock.advance(Decimal("2"))
    store.receive("fake_public", b'{"seq":4}', source_seq="4")
    store.mark_gap("fake_public", "disconnected")
    store.mark_reconnect("fake_public")
    store.receive("fake_public", b'{"seq":1}', source_seq="1")
    records = rows(store)
    assert [record["kind"] for record in records] == [
        "DATA",
        "DUPLICATE",
        "GAP",
        "DATA",
        "GAP",
        "RECONNECT",
        "DATA",
    ]
    assert records[2]["details"] == "source sequence missing: 2..3"
    assert len(list((store.root / "payloads").glob("*.raw"))) == 7
    assert (store.root / records[1]["envelope"]["payload_ref"]).read_bytes() == b'{"seq":1}'


def test_payload_hash_mismatch_cannot_corrupt_manifest(tmp_path):
    store, clock = capture(tmp_path)
    event = EventEnvelope(
        "id",
        "run-offline",
        None,
        "fake",
        None,
        None,
        clock.utc_now(),
        clock.utc_now(),
        1,
        "payloads/id.raw",
        "a" * 64,
    )
    with pytest.raises(ValueError, match="hash"):
        store.append_raw(event, b"different")
    assert rows(store) == []


@pytest.mark.parametrize("ref", ["../outside.raw", "/tmp/outside.raw", "payloads/../outside.raw"])
def test_payload_reference_cannot_escape_capture_root(tmp_path, ref):
    store, clock = capture(tmp_path)
    payload = b"data"
    event = EventEnvelope(
        "id",
        "run-offline",
        None,
        "fake",
        None,
        None,
        clock.utc_now(),
        clock.utc_now(),
        1,
        ref,
        hashlib.sha256(payload).hexdigest(),
    )
    with pytest.raises(ValueError, match="reference"):
        store.append_raw(event, payload)
    assert rows(store) == []


def test_second_writer_and_existing_capture_are_not_overwritten(tmp_path):
    store, clock = capture(tmp_path)
    store.receive("fake", b"first")
    with pytest.raises(FileExistsError):
        api("RawCapture")(store.root, clock, "run-offline")
    assert len(rows(store)) == 1


def test_duplicate_external_event_does_not_overwrite_payload(tmp_path):
    store, _ = capture(tmp_path)
    event = store.receive("fake", b"first")
    with pytest.raises(FileExistsError):
        store.append_raw(event, b"first")
    assert len(rows(store)) == 1


@pytest.mark.parametrize(
    "kwargs,first,next_payload,reason",
    [
        ({"max_events": 1}, b"one", b"two", "max_events"),
        ({"max_payload_bytes": 4}, b"one", b"12345", "max_payload_bytes"),
        ({"max_total_payload_bytes": 5}, b"one", b"four", "max_total_payload_bytes"),
    ],
)
def test_capture_is_bounded_and_records_explicit_stop_gap(
    tmp_path, kwargs, first, next_payload, reason
):
    store, _ = capture(tmp_path, **kwargs)
    store.receive("fake", first)
    with pytest.raises(api("CaptureLimitError")):
        store.receive("fake", next_payload)
    assert len(rows(store)) == 1
    stopped = json.loads((store.root / "capture-stop.json").read_text())
    assert stopped["coverage"] == "GAP"
    assert stopped["reason"] == reason


class FakePublicClient:
    def __init__(self, results):
        self.results = iter(results)

    def fetch_instruments(self, environment):
        assert environment == "production_public"
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_bounded_collector_keeps_poll_gaps_and_never_retries_failure(tmp_path):
    store, _ = capture(tmp_path)
    client = FakePublicClient(
        [b'{"code":0,"result":{"data":[]}}', OSError("offline"), b'{"code":0,"result":{"data":[]}}']
    )
    report = api("collect_instruments")(store, client, "production_public", polls=3)
    assert report["attempts"] == 3
    assert report["successes"] == 2
    assert report["failures"] == 1
    records = rows(store)
    assert [row["kind"] for row in records] == ["DATA", "GAP", "GAP", "GAP", "DUPLICATE"]
    assert records[2]["details"] == "poll failed: OSError"


def test_collector_does_not_label_malformed_json_as_valid_data(tmp_path):
    store, _ = capture(tmp_path)
    client = FakePublicClient([b"<html>error</html>"])
    report = api("collect_instruments")(store, client, "production_public", polls=1)
    assert report["failures"] == 1
    records = rows(store)
    assert (
        store.root / records[0]["envelope"]["payload_ref"]
    ).read_bytes() == b"<html>error</html>"
    assert records[-1]["kind"] == "GAP"


@pytest.mark.parametrize("environment", ["https://evil.example", "live", "private", "uat"])
def test_public_client_rejects_unapproved_endpoint_or_environment(environment):
    client = api("PublicInstrumentsClient")()
    with pytest.raises(ValueError, match="environment"):
        client.fetch_instruments(environment)


def test_public_client_rejects_redirect_without_following_it():
    handler = api("NoRedirect")()
    assert handler.redirect_request(None, None, 302, "redirect", {}, "https://evil.example") is None


def test_public_request_is_fixed_anonymous_get_without_proxy_or_auth(monkeypatch):
    import io
    from urllib.request import ProxyHandler

    import trading_bot.data.raw_capture as module

    observed = []

    class Transport:
        def open(self, request, timeout):
            observed.append(
                (request.full_url, request.get_method(), request.header_items(), timeout)
            )
            return io.BytesIO(b'{"code":0,"result":{"data":[]}}')

    def opener(*handlers):
        proxy = next(handler for handler in handlers if isinstance(handler, ProxyHandler))
        assert proxy.proxies == {}
        assert any(isinstance(handler, module.NoRedirect) for handler in handlers)
        return Transport()

    monkeypatch.setattr(module, "build_opener", opener)
    body = module.PublicInstrumentsClient().fetch_instruments("uat_public")
    assert body == b'{"code":0,"result":{"data":[]}}'
    assert observed == [
        (
            "https://uat-api.3ona.co/exchange/v1/public/get-instruments",
            "GET",
            [
                ("Accept", "application/json"),
                ("User-agent", "crypto-trading-bot-p01-public-capture/0.1"),
            ],
            10,
        )
    ]


def test_public_response_byte_limit_is_enforced_before_returning_partial_data():
    import io

    client = api("PublicInstrumentsClient")(max_response_bytes=4)
    with pytest.raises(api("CaptureLimitError"), match="incomplete"):
        client._read(io.BytesIO(b"12345"))


def test_http_failure_keeps_original_error_body_and_marks_gap(tmp_path):
    store, _ = capture(tmp_path)
    failure = api("PublicFetchError")(429, b'{"code":429,"message":"rate limited"}')
    client = FakePublicClient([failure])
    report = api("collect_instruments")(store, client, "production_public", polls=1)
    assert report == {"attempts": 1, "successes": 0, "failures": 1}
    record = rows(store)[0]
    assert (store.root / record["envelope"]["payload_ref"]).read_bytes() == failure.payload
    assert rows(store)[-1]["kind"] == "GAP"


def test_failed_public_polls_obey_same_explicit_rate_bound(tmp_path, monkeypatch):
    import trading_bot.data.raw_capture as module

    store, _ = capture(tmp_path)
    sequence = []

    class FailedClient(module.PublicInstrumentsClient):
        def fetch_instruments(self, environment):
            sequence.append("fetch")
            raise OSError("unreachable")

    monkeypatch.setattr(module.time, "sleep", lambda seconds: sequence.append(("sleep", seconds)))
    report = module.collect_instruments(store, FailedClient(), "production_public", polls=3)
    assert report["failures"] == 3
    assert sequence == ["fetch", ("sleep", 1), "fetch", ("sleep", 1), "fetch"]


def test_receipt_monotonic_is_capture_offset_not_system_boot_time(tmp_path):
    from decimal import localcontext

    clock = SimulationClock(datetime(2026, 1, 1, tzinfo=UTC))
    clock.advance(Decimal("1234567890.123456789"))
    store = api("RawCapture")(tmp_path / "session", clock, "run-offset")
    clock.advance(Decimal("0.000000001"))
    with localcontext() as context:
        context.prec = 5
        event = store.receive("fake", b"data")
    assert event.received_monotonic == Decimal("0.000000001")


def test_metadata_cannot_bypass_capture_byte_bounds(tmp_path):
    store, _ = capture(tmp_path)
    with pytest.raises(api("CaptureLimitError")):
        store.receive("x" * 20_000, b"data")
    assert rows(store) == []
    assert json.loads((store.root / "capture-stop.json").read_text())["coverage"] == "GAP"
