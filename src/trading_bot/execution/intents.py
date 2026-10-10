"""Validate synthetic authority and freeze an exact fake-only create payload."""

import json
from hashlib import sha256

from trading_bot.domain.records import OrderIntent
from trading_bot.domain.serialization import utc


def prepare_intent(approval, reservation, payload, *, now):
    if approval.side != "BUY" or reservation.side != "BUY":
        raise ValueError("entry API is BUY only; SELL requires exit coordination")
    now = utc(now)
    if (
        approval.consumed
        or approval.expires_at <= now
        or reservation.expires_at <= now
        or not approval.approval_id
        or not approval.profile_hash
        or approval.ledger_version != reservation.ledger_version
        or approval.instrument != reservation.instrument
        or approval.side != reservation.side
        or approval.max_quantity.base != reservation.quantity.base
        or approval.max_quantity.amount < reservation.quantity.amount
        or approval.max_cash.currency != reservation.cash.currency
        or approval.max_cash.amount < reservation.cash.amount
        or approval.risk != reservation.risk
        or approval.price_bound.currency != reservation.instrument.quote
        or approval.price_bound.amount <= 0
    ):
        raise ValueError("fixture approval/reservation scope, bounds or expiry conflict")
    if reservation.side == "BUY":
        from trading_bot.accounting.arithmetic import mul

        if mul(reservation.quantity.amount, approval.price_bound.amount) > reservation.cash.amount:
            raise ValueError("price bound exceeds reserved cash")
    wire = json.loads(payload)
    # This deliberately tiny profile is a fake-only command, not exchange wire format.
    expected = {"client_order_id": reservation.order_id, "type": "LIMIT", "time_in_force": "FOK"}
    if wire != expected:
        raise ValueError("payload conflicts with immutable client ID or fake FOK profile")
    return OrderIntent(
        reservation.intent_id,
        reservation.account_id,
        reservation.instrument,
        reservation.side,
        reservation.quantity,
        approval.price_bound,
        sha256(payload.encode()).hexdigest(),
        approval.approval_id,
        reservation.reservation_id,
        "PREPARED",
    )
