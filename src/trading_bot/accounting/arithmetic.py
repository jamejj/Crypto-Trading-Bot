"""Financial products do not inherit the caller's Decimal precision."""

from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal, localcontext

from trading_bot.domain.serialization import decimal, exact_add

ZERO = Decimal("0")


def mul(a, b):
    decimal(a)
    decimal(b)
    with localcontext(
        Context(
            prec=max(1, len(a.as_tuple().digits) + len(b.as_tuple().digits)),
            Emax=MAX_EMAX,
            Emin=MIN_EMIN,
        )
    ):
        return a * b


def div(a, b):
    # Unit ratios may recur: deterministic 80-digit accounting precision.
    with localcontext(Context(prec=80, Emax=MAX_EMAX, Emin=MIN_EMIN)):
        return a / b


def sub(a, b):
    return exact_add(a, b.copy_negate())


def total(values):
    result = ZERO
    for value in values:
        result = exact_add(result, value)
    return result


def proportional(value, part, whole):
    return div(mul(value, part), whole)
