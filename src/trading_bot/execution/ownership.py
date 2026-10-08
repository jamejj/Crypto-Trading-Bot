"""Manual, fake-only writer grants with an independent transmission fence.

The authority represents a single external egress gate shared by ALL senders.
It is not a production credential/network fence and cannot verify Crypto.com.
DB ownership alone cannot create a channel. Tokens read from DB cannot open one.
"""

import json
from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

import psycopg

from trading_bot.domain.serialization import Record
from trading_bot.storage.transactions import transaction


class OwnershipLost(PermissionError):
    pass


@dataclass(frozen=True)
class WriterToken(Record):
    account_id: str
    runtime_id: str
    owner_id: str
    epoch: int
    authority_id: str
    nonce: str


@dataclass(frozen=True)
class CutoffReceipt:
    token: WriterToken
    receipt_id: str


class FakeFenceAuthority:
    """Linearized egress fence independent of DB lease/state; closed by default.

    cut_off waits for already-admitted callbacks, then blocks ALL subsequent ones.
    This models an external isolation barrier, not cancellation of an in-flight RPC.
    Losing/replacing this authority is never an automatic restart or failover path.
    """

    def __init__(self):
        self.authority_id = "synthetic:fence:" + uuid4().hex
        self._lock = RLock()
        self._active = {}
        self._receipts = {}
        self._seen = set()
        self._fenced_epochs = {}

    def active_token(self, account):
        with self._lock:
            value = self._active.get(account)
            return value[0] if value else None

    def cut_off(self, token):
        with self._lock:
            if token.authority_id != self.authority_id:
                raise OwnershipLost("foreign fencing authority")
            active = self._active.get(token.account_id)
            if active and active[0] != token:
                raise OwnershipLost("cannot cut off another generation")
            self._active.pop(token.account_id, None)
            self._fenced_epochs[token.account_id] = max(
                token.epoch, self._fenced_epochs.get(token.account_id, 0)
            )
            self._seen.add(token.account_id)
            receipt = CutoffReceipt(token, uuid4().hex)
            self._receipts[receipt.receipt_id] = receipt
            return receipt

    def _proof(self, token, receipt):
        if (
            receipt is None
            or self._receipts.get(receipt.receipt_id) is not receipt
            or receipt.token != token
            or token.authority_id != self.authority_id
            or token.account_id in self._active
        ):
            raise OwnershipLost("positive external cutoff receipt required")

    def _activate(self, token, receipt=None):
        with self._lock:
            if token.epoch <= self._fenced_epochs.get(token.account_id, 0):
                raise OwnershipLost("generation permanently fenced")
            if token.account_id in self._active:
                raise OwnershipLost("external sender already active")
            if receipt is None:
                if token.account_id in self._seen:
                    raise OwnershipLost("external scope requires manual cutoff")
            else:
                self._proof(receipt.token, receipt)
                if token.epoch <= receipt.token.epoch:
                    raise OwnershipLost("new generation required")
                del self._receipts[receipt.receipt_id]
            permit = object()
            self._active[token.account_id] = (token, permit)
            self._seen.add(token.account_id)
            return permit

    def _check(self, token, permit):
        value = self._active.get(token.account_id)
        if value is None or value[0] != token or value[1] is not permit:
            raise OwnershipLost("transmission channel fenced")

    def _send(self, token, permit, callback):
        with self._lock:
            self._check(token, permit)
            return callback()


class OwnershipControl:
    """Operator API only. Constructors, expiry and recovery never grant ownership."""

    def __init__(self, connection_factory, account_id, authority, *, checkpoint=None):
        if type(authority) is not FakeFenceAuthority:
            raise TypeError("synthetic fencing authority required")
        self.connect, self.account_id, self.authority = connection_factory, account_id, authority
        self.checkpoint = checkpoint or (lambda stage: None)

    def _row(self, conn, *, lock=False):
        row = conn.execute(
            "SELECT token,status FROM execution_ownership WHERE account_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (self.account_id,),
        ).fetchone()
        if row is None:
            return None
        return WriterToken.from_json(json.dumps(row[0])), row[1]

    def current(self):
        with transaction(self.connect) as conn:
            row = self._row(conn)
            return row[0] if row else None

    def _token(self, runtime, owner, epoch):
        if not runtime or not owner:
            raise ValueError("runtime and process identity required")
        return WriterToken(
            self.account_id, runtime, owner, epoch, self.authority.authority_id, uuid4().hex
        )

    def _audit(self, conn, token, action, reason):
        if not reason.strip():
            raise ValueError("operator reason required")
        conn.execute(
            "INSERT INTO execution_ownership_audit(account_id,token,action,reason) "
            "VALUES (%s,%s::jsonb,%s,%s)",
            (self.account_id, token.to_json(), action, reason),
        )

    def manual_initial(self, runtime, owner, reason):
        token = self._token(runtime, owner, 1)
        with self.authority._lock:
            if self.account_id in self.authority._seen:
                raise OwnershipLost("external scope already bound; manual recovery required")
        with transaction(self.connect) as conn:
            self.checkpoint("BEFORE_RECORD")
            inserted = conn.execute(
                "INSERT INTO execution_ownership VALUES (%s,%s::jsonb,'ACTIVE') "
                "ON CONFLICT DO NOTHING RETURNING 1",
                (self.account_id, token.to_json()),
            ).fetchone()
            if not inserted:
                raise OwnershipLost("ownership already recorded; no automatic takeover")
            self._audit(conn, token, "GRANT", reason)
            self.checkpoint("RECORDED_UNCOMMITTED")
        self.checkpoint("RECORD_COMMITTED")
        permit = self.authority._activate(token)
        self.checkpoint("CHANNEL_ACTIVE")
        return WriterGuard(self, token, permit)

    def manual_revoke(self, token, receipt, reason):
        # Same lock order as send: ownership row -> independent authority.
        with transaction(self.connect) as conn:
            row = self._row(conn, lock=True)
            if row is None or row[0] != token:
                raise OwnershipLost("stale revoke")
            with self.authority._lock:
                self.authority._proof(token, receipt)
                if row[1] == "REVOKED":
                    return
                conn.execute(
                    "UPDATE execution_ownership SET status='REVOKED' WHERE account_id=%s",
                    (self.account_id,),
                )
                self._audit(conn, token, "REVOKE", reason)

    def manual_takeover(self, token, receipt, runtime, owner, reason):
        with transaction(self.connect) as conn:
            row = self._row(conn, lock=True)
            if row != (token, "REVOKED"):
                raise OwnershipLost("durable revoke required before manual takeover")
            with self.authority._lock:
                self.authority._proof(token, receipt)
                new = self._token(runtime, owner, token.epoch + 1)
                conn.execute(
                    "UPDATE execution_ownership SET token=%s::jsonb,status='ACTIVE' "
                    "WHERE account_id=%s",
                    (new.to_json(), self.account_id),
                )
                self._audit(conn, new, "GRANT", reason)
                self.checkpoint("RECORDED_UNCOMMITTED")
        self.checkpoint("RECORD_COMMITTED")
        permit = self.authority._activate(new, receipt)
        self.checkpoint("CHANNEL_ACTIVE")
        return WriterGuard(self, new, permit)


class WriterGuard:
    """Non-recoverable process handle; a DB token is insufficient to construct one."""

    def __init__(self, control, token, permit=None):
        self.control, self.token, self._permit = control, token, permit
        self.stopped = False

    def _validate(self, conn, account, *, lock=False):
        if self.stopped or account != self.token.account_id or account != self.control.account_id:
            raise OwnershipLost("writer stopped or wrong account")
        if self.control._row(conn, lock=lock) != (self.token, "ACTIVE"):
            raise OwnershipLost("durable writer ownership lost")

    def check(self, account):
        try:
            with transaction(self.control.connect) as conn:
                self._validate(conn, account)
                with self.control.authority._lock:
                    self.control.authority._check(self.token, self._permit)
        except (OwnershipLost, psycopg.Error):
            self.stopped = True
            raise

    def send(self, account, callback):
        try:
            with transaction(self.control.connect) as conn:
                self._validate(conn, account, lock=True)
                return self.control.authority._send(self.token, self._permit, callback)
        except (OwnershipLost, psycopg.Error):
            self.stopped = True
            raise


def require_writer(writer):
    if type(writer) is not WriterGuard:
        raise OwnershipLost("explicit authenticated synthetic writer handle required")
    return writer
