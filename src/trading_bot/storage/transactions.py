"""Canonical versioned journal codec; domain records retain typed financial values."""

import json
from contextlib import contextmanager

from trading_bot.domain.serialization import _decode, _encode

METHODS = {"_cashflow", "_fill", "_fee", "_reserve", "_release", "_value", "_dust"}


def encode_arguments(arguments):
    return json.dumps(_encode(arguments), sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_arguments(method, payload):
    if method not in METHODS:
        raise ValueError("unknown ledger journal operation")
    arguments = _decode(payload)
    if not isinstance(arguments, tuple):
        raise ValueError("journal arguments must be immutable tuple")
    return arguments


@contextmanager
def transaction(connection_factory):
    with connection_factory() as conn:
        # Explicit transaction is mandatory even for an autocommit factory.
        with conn.transaction():
            yield conn
