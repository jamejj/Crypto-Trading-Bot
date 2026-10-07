"""Append original bytes and explicit gaps; only P00-observed public instrument catalogs.

Each directory belongs to one capture session. Existing sessions are never resumed or overwritten.
No order normalization, strategy signals, authentication, redirect, proxy, or retry exists here.
"""

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from trading_bot.config.profiles import PUBLIC_INSTRUMENTS_URLS
from trading_bot.domain.events import EventEnvelope
from trading_bot.domain.serialization import exact_add
from trading_bot.operations.clock import Clock, RealClock

_ENVIRONMENTS = dict(zip(("production_public", "uat_public"), PUBLIC_INSTRUMENTS_URLS, strict=True))


class CaptureLimitError(RuntimeError):
    pass


class RawCapture:
    def __init__(
        self,
        root: Path,
        clock: Clock,
        run_id: str,
        *,
        max_events: int = 1000,
        max_payload_bytes: int = 2_000_000,
        max_total_payload_bytes: int = 100_000_000,
    ):
        if any(
            type(limit) is not int or limit <= 0
            for limit in (max_events, max_payload_bytes, max_total_payload_bytes)
        ):
            raise ValueError("positive integer capture limits required")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            raise ValueError("safe run identity required")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload_dir = self.root / "payloads"
        if payload_dir.is_symlink():
            raise ValueError("payload directory must not be a symlink")
        payload_dir.mkdir(exist_ok=True, mode=0o700)
        # Exclusive creation is the session ownership guard, not a recoverable trading writer lease.
        with (self.root / "events.jsonl").open("x", encoding="utf-8"):
            pass
        self.clock = clock
        self._monotonic_origin = clock.monotonic_now()
        self.run_id = run_id
        self.max_events = max_events
        self.max_payload_bytes = max_payload_bytes
        self.max_total_payload_bytes = max_total_payload_bytes
        self._event_count = 0
        self._payload_bytes = 0
        self._next_id = 0
        self._seen: set[tuple[str, str | None, str]] = set()
        self._last_sequence: dict[str, int] = {}
        self._stopped = False

    def _stop(self, reason: str) -> None:
        self._stopped = True
        with (self.root / "capture-stop.json").open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "coverage": "GAP",
                    "reason": reason,
                    "received_at": self.clock.utc_now().isoformat(),
                    "events": self._event_count,
                    "payload_bytes": self._payload_bytes,
                },
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        raise CaptureLimitError(reason)

    def append_raw(
        self, event: EventEnvelope, payload: bytes, *, kind: str = "DATA", details: str = ""
    ) -> str:
        if self._stopped:
            raise CaptureLimitError("capture already stopped")
        if not isinstance(payload, bytes):
            raise TypeError("original payload must be bytes")
        if event.run_id != self.run_id or event.account_id is not None:
            raise ValueError("raw public capture requires this run and no account")
        if kind not in {"DATA", "DUPLICATE", "GAP", "RECONNECT"}:
            raise ValueError("unknown capture kind")
        if len(details) > 256:
            raise ValueError("capture details exceed limit")
        if not re.fullmatch(r"payloads/[A-Za-z0-9_-]{1,100}\.raw", event.payload_ref):
            raise ValueError("unsafe payload reference")
        if hashlib.sha256(payload).hexdigest() != event.payload_hash:
            raise ValueError("original payload hash mismatch")
        record = {"envelope": json.loads(event.to_json()), "kind": kind, "details": details}
        encoded_record = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        if len(encoded_record.encode("utf-8")) > 4096:
            self._stop("max_metadata_bytes")
        if self._event_count >= self.max_events:
            self._stop("max_events")
        if len(payload) > self.max_payload_bytes:
            self._stop("max_payload_bytes")
        if self._payload_bytes + len(payload) > self.max_total_payload_bytes:
            self._stop("max_total_payload_bytes")
        with (self.root / event.payload_ref).open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(encoded_record)
            stream.flush()
            os.fsync(stream.fileno())
        self._event_count += 1
        self._payload_bytes += len(payload)
        return event.payload_ref

    def _record(
        self,
        source: str,
        payload: bytes,
        kind: str,
        details: str,
        source_seq: str | None = None,
        event_time: datetime | None = None,
    ) -> EventEnvelope:
        self._next_id += 1
        event_id = f"{self.run_id}-{self._next_id:06}"
        now = self.clock.utc_now()
        event = EventEnvelope(
            event_id,
            self.run_id,
            None,
            source,
            source_seq,
            event_time,
            now,
            now,
            1,
            f"payloads/{event_id}.raw",
            hashlib.sha256(payload).hexdigest(),
            exact_add(self.clock.monotonic_now(), self._monotonic_origin.copy_negate()),
        )
        self.append_raw(event, payload, kind=kind, details=details)
        return event

    def receive(
        self,
        source: str,
        payload: bytes,
        *,
        source_seq: str | None = None,
        event_time: datetime | None = None,
    ) -> EventEnvelope:
        if not isinstance(payload, bytes):
            raise TypeError("original payload must be bytes")
        digest = hashlib.sha256(payload).hexdigest()
        identity = (source, source_seq, digest)
        duplicate = identity in self._seen
        if source_seq is not None and source_seq.isascii() and source_seq.isdigit():
            sequence = int(source_seq)
            previous = self._last_sequence.get(source)
            if previous is not None and sequence > previous + 1:
                self.mark_gap(source, f"source sequence missing: {previous + 1}..{sequence - 1}")
            self._last_sequence[source] = max(
                sequence, previous if previous is not None else sequence
            )
        event = self._record(
            source, payload, "DUPLICATE" if duplicate else "DATA", "", source_seq, event_time
        )
        self._seen.add(identity)
        return event

    def mark_gap(self, source: str, reason: str) -> EventEnvelope:
        payload = json.dumps({"gap": reason}, sort_keys=True).encode("utf-8")
        return self._record(source, payload, "GAP", reason)

    def mark_reconnect(self, source: str) -> EventEnvelope:
        self._last_sequence.pop(source, None)
        self._seen = {identity for identity in self._seen if identity[0] != source}
        return self._record(
            source, b'{"reconnect":true}', "RECONNECT", "new session; continuity unknown"
        )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicFetchError(OSError):
    def __init__(self, status: int, payload: bytes):
        super().__init__(f"public HTTP status {status}")
        self.payload = payload


class PublicInstrumentsClient:
    def __init__(self, *, timeout_seconds: int = 10, max_response_bytes: int = 2_000_000):
        if (
            type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 30
            or type(max_response_bytes) is not int
            or not 1 <= max_response_bytes <= 2_000_000
        ):
            raise ValueError("bounded timeout and response limits required")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def _read(self, response) -> bytes:
        deadline = time.monotonic() + self.timeout_seconds
        chunks = []
        total = 0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("public body deadline elapsed")
            chunk = response.read1(min(65_536, self.max_response_bytes - total + 1))
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self.max_response_bytes:
                raise CaptureLimitError("public response exceeds byte limit; body incomplete")
            chunks.append(chunk)

    def fetch_instruments(self, environment: str) -> bytes:
        if environment not in _ENVIRONMENTS:
            raise ValueError("unapproved public environment")
        # Empty proxy config prevents environment proxy credentials; opener carries no cookies/auth.
        opener = build_opener(ProxyHandler({}), NoRedirect())
        request = Request(
            _ENVIRONMENTS[environment],
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "crypto-trading-bot-p01-public-capture/0.1",
            },
        )
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                return self._read(response)
        except HTTPError as error:
            with error:
                body = self._read(error)
            # A redirect body may be archived; Location is never followed or exposed as an endpoint.
            raise PublicFetchError(error.code, body) from error


class PublicClient(Protocol):
    def fetch_instruments(self, environment: str) -> bytes: ...


def collect_instruments(
    store: RawCapture, client: PublicClient, environment: str, *, polls: int = 1
) -> dict[str, int]:
    if environment not in _ENVIRONMENTS or type(polls) is not int or not 1 <= polls <= 100:
        raise ValueError("approved environment and polls 1..100 required")
    source = f"crypto_com:{environment}:get-instruments"
    report = {"attempts": 0, "successes": 0, "failures": 0}
    for index in range(polls):
        if index:
            if isinstance(client, PublicInstrumentsClient):
                time.sleep(1)
            store.mark_gap(source, "catalog polling is not continuous market coverage")
        report["attempts"] += 1
        try:
            payload = client.fetch_instruments(environment)
        except CaptureLimitError:
            store.mark_gap(source, "response incomplete: byte limit")
            raise
        except OSError as error:
            if isinstance(error, PublicFetchError):
                store.receive(source, error.payload)
            store.mark_gap(source, f"poll failed: {type(error).__name__}")
            report["failures"] += 1
            continue
        store.receive(source, payload)
        try:
            data = json.loads(payload)
            valid = (
                isinstance(data, dict)
                and type(data.get("code")) is int
                and data["code"] == 0
                and isinstance(data.get("result"), dict)
                and isinstance(data["result"].get("data"), list)
            )
        except (ValueError, UnicodeDecodeError):
            valid = False
        if valid:
            report["successes"] += 1
        else:
            store.mark_gap(source, "invalid public catalog response; original bytes preserved")
            report["failures"] += 1
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded anonymous get-instruments raw capture")
    parser.add_argument("--environment", choices=tuple(_ENVIRONMENTS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--polls", type=int, default=1)
    args = parser.parse_args(argv)
    if not 1 <= args.polls <= 100:
        parser.error("polls must be 1..100")
    store = RawCapture(args.output, RealClock(), args.run_id)
    report = collect_instruments(
        store, PublicInstrumentsClient(), args.environment, polls=args.polls
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
