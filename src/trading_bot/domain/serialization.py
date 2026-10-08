"""Version-one typed JSON contracts; never coerce financial floats."""

import json
import types
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal, localcontext
from enum import Enum
from typing import get_args, get_origin, get_type_hints

_TYPES: dict[str, type] = {}


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError("financial values must be Decimal")
    if not value.is_finite():
        raise ValueError("Decimal must be finite")
    return value


def exact_add(left: Decimal, right: Decimal) -> Decimal:
    decimal(left)
    decimal(right)
    low = min(left.as_tuple().exponent, right.as_tuple().exponent)
    high = max(left.adjusted(), right.adjusted())
    with localcontext(Context(prec=max(1, high - low + 2), Emax=MAX_EMAX, Emin=MIN_EMIN)):
        return left + right


def _validate(value, annotation):
    if hasattr(annotation, "__supertype__"):
        annotation = annotation.__supertype__
    origin = get_origin(annotation)
    if origin is types.UnionType:
        for option in get_args(annotation):
            try:
                _validate(value, option)
                return
            except (TypeError, ValueError):
                pass
        raise TypeError(f"value does not match {annotation}")
    if origin is tuple:
        if not isinstance(value, tuple):
            raise TypeError("immutable tuple required")
        args = get_args(annotation)
        if len(args) == 2 and args[1] is Ellipsis:
            for item in value:
                _validate(item, args[0])
        else:
            if len(args) != len(value):
                raise TypeError("tuple arity mismatch")
            for item, kind in zip(value, args, strict=True):
                _validate(item, kind)
    elif annotation is Decimal:
        decimal(value)
    elif annotation is datetime:
        utc(value)
    elif annotation is int:
        if type(value) is not int:
            raise TypeError("integer required")
    elif not isinstance(value, annotation):
        raise TypeError(f"expected {annotation}, got {type(value)}")


def _encode(value):
    if isinstance(value, Decimal):
        return {"$decimal": str(decimal(value))}
    if isinstance(value, datetime):
        return {"$utc": utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")}
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            "$type": type(value).__name__,
            "$schema": getattr(type(value), "SCHEMA_VERSION", 1),
            **{f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if isinstance(value, float):
        raise TypeError("float is not a domain wire value")
    return value


def _decode(value):
    if isinstance(value, list):
        return tuple(_decode(item) for item in value)
    if isinstance(value, dict):
        if set(value) == {"$decimal"}:
            if not isinstance(value["$decimal"], str):
                raise TypeError("wire Decimal requires a string")
            return decimal(Decimal(value["$decimal"]))
        if set(value) == {"$utc"}:
            return utc(datetime.fromisoformat(value["$utc"]))
        if "$type" in value:
            kind = _TYPES.get(value["$type"])
            if kind is None:
                raise ValueError("unknown record type")
            if type(value.get("$schema")) is not int or value["$schema"] not in getattr(
                kind, "READABLE_SCHEMAS", (1,)
            ):
                raise ValueError("unsupported record wire schema")
            return kind(
                **{
                    key: _decode(item)
                    for key, item in value.items()
                    if key not in {"$type", "$schema"}
                }
            )
        raise ValueError("untyped object in domain wire data")
    if isinstance(value, float):
        raise TypeError("float is not a domain wire value")
    return value


class Record:
    """Immutable dataclasses inherit strict wire serialization and boundary validation."""

    def __init_subclass__(cls):
        _TYPES[cls.__name__] = cls

    def __post_init__(self):
        hints = get_type_hints(type(self))
        for field in fields(self):
            value = getattr(self, field.name)
            _validate(value, hints[field.name])
            if isinstance(value, datetime):
                object.__setattr__(self, field.name, utc(value))

    def to_json(self) -> str:
        return json.dumps(_encode(self), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, payload: str):
        record = _decode(json.loads(payload))
        if type(record) is not cls:
            raise ValueError("unexpected record type")
        return record
