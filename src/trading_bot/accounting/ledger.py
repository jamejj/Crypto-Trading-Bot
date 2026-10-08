"""Deterministic append-only double-entry accounting with atomic local operations."""

from copy import deepcopy
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256

from trading_bot.domain.money import Money, Quantity
from trading_bot.domain.records import InventoryLot, LedgerSnapshot
from trading_bot.domain.serialization import Record, exact_add

from .arithmetic import ZERO, div, mul, proportional, sub, total
from .inventory import consume
from .reservations import commitments, empty, validate


@dataclass(frozen=True)
class Posting(Record):
    operation: str
    account: str
    value: Money


class Ledger:
    def __init__(self, account_id, quote):
        if not account_id or not quote:
            raise ValueError("account and quote required")
        self.account_id, self.quote = account_id, quote
        self.entries = ()
        self.operations = ()
        self.balances = {}
        self.lots = []
        self.reservations = {}
        self.fills = {}
        self.fees = {}
        self.flow_ids = {}
        self.unknown_fees = {}
        self.unknown_components = {}
        self.version = 0
        self.units = ZERO
        self.nav = Money(ZERO, quote)
        self.hwm = Money(ZERO, quote)
        self.reasons = ()

    @property
    def snapshot(self):
        return LedgerSnapshot(
            self.version,
            tuple(Money(v, k) for k, v in sorted(self.balances.items())),
            tuple(self.lots),
            tuple(Money(v, k) for k, v in sorted(self.unknown_fees.items()) if v),
            tuple(self.reservations[k] for k in sorted(self.reservations)),
            self.nav,
            self.units,
            self.hwm,
            self.reasons,
        )

    def _atomic(self, method, *args):
        candidate = deepcopy(self)
        changed = getattr(candidate, method)(*args)
        if changed is False:
            return self.snapshot
        for currency in candidate.balances:
            if candidate._available(currency) < ZERO:
                raise ValueError("available assets below surviving commitments")
        owned = {}
        for lot in candidate.lots:
            currency = lot.quantity.base
            owned[currency] = exact_add(owned.get(currency, ZERO), lot.quantity.amount)
        for currency in set(candidate.balances) | set(owned):
            if currency != candidate.quote and candidate.balances.get(currency, ZERO) != owned.get(
                currency, ZERO
            ):
                raise ValueError("inventory ownership disagrees with asset balance")
        candidate.version += 1
        candidate.operations += ((method, args),)
        self.__dict__.update(candidate.__dict__)
        return self.snapshot

    def _post(self, operation, currency, amount, counterparty):
        self.balances[currency] = exact_add(self.balances.get(currency, ZERO), amount)
        self.entries += (
            Posting(operation, "asset:" + currency, Money(amount, currency)),
            Posting(operation, counterparty, Money(amount.copy_negate(), currency)),
        )

    def _available(self, currency, order=None):
        return sub(
            sub(
                self.balances.get(currency, ZERO),
                commitments(self.reservations.values(), currency, order),
            ),
            self.unknown_fees.get(currency, ZERO),
        )

    def _check_debit(self, currency, amount, order=None):
        if amount > self._available(currency, order):
            raise ValueError("available assets insufficient; no borrowing")

    def post_cashflow(self, source_id, value, quote_value=None, evidence=None):
        return self._atomic("_cashflow", source_id, value, quote_value, evidence)

    def _cashflow(self, source_id, value, quote_value, evidence):
        identity = (value, quote_value)
        if source_id in self.flow_ids:
            if self.flow_ids[source_id] != identity:
                raise ValueError("cashflow conflict")
            return False
        if not source_id:
            raise ValueError("cashflow identity required")
        if (
            evidence is None
            or not evidence.evidence_id
            or not evidence.paused
            or not evidence.reconciled
            or evidence.account_id != self.account_id
            or evidence.ledger_version != self.version
        ):
            raise ValueError(
                "funding requires scoped pause/reconciliation evidence at ledger version"
            )
        if value.currency != self.quote:
            raise ValueError("v1 external funding supports quote currency only")
        q = value if value.currency == self.quote else quote_value
        if q is None or q.currency != self.quote or (q.amount < ZERO) != (value.amount < ZERO):
            raise ValueError("cashflow requires reconciled quote value")
        if self.flow_ids and self.reasons:
            raise ValueError("cashflow requires certain reconciled NAV")
        if value.amount < ZERO:
            self._check_debit(value.currency, value.amount.copy_negate())
        unit_nav = self.nav.amount
        if self.flow_ids:
            if unit_nav <= ZERO:
                raise ValueError("cashflow requires positive unit NAV")
            units = exact_add(self.units, div(q.amount, unit_nav))
        else:
            if q.amount <= ZERO:
                raise ValueError("initial funding must be positive")
            units = q.amount
            self.nav = self.hwm = Money(Decimal("1"), self.quote)
        if units < ZERO:
            raise ValueError("withdrawal exceeds units")
        self.units = units
        self._post(source_id, value.currency, value.amount, "external:funding")
        self.flow_ids[source_id] = identity
        if value.currency != self.quote:
            self.reasons = ("UNPRICED:" + value.currency,)

    def post_fill(self, fill):
        return self._atomic("_fill", fill)

    def _lot_id(self, fill, suffix="acquisition"):
        identity = "\0".join((self.account_id, fill.instrument.to_json(), fill.trade_id, suffix))
        return "lot:" + sha256(identity.encode()).hexdigest()

    def _protected_inventory(self):
        protected = {}
        for key, amount in self.unknown_components.items():
            account, instrument, trade_id, currency = key
            fill = self.fills.get((account, instrument, trade_id))
            if fill is not None and fill.side == "BUY" and currency == instrument.base:
                protected[self._lot_id(fill)] = amount
        return protected

    def _key(self, fill):
        return fill.account_id, fill.instrument, fill.trade_id

    def _fill(self, fill):
        if (
            fill.account_id != self.account_id
            or not fill.trade_id
            or not fill.order_id
            or fill.side not in {"BUY", "SELL"}
            or fill.quantity.amount <= ZERO
            or fill.price.amount <= ZERO
            or fill.instrument.quote != self.quote
        ):
            raise ValueError("fill requires stable economic identity and positive spot values")
        key = self._key(fill)
        if any((fee.account_id, fee.instrument, fee.fill_id) != key for fee in fill.fees):
            raise ValueError("embedded fee identity must match containing fill scope")
        if key in self.fills:
            prior = self.fills[key]

            def economic(f):
                return f.order_id, f.side, f.quantity, f.price, f.event_time

            if economic(prior) != economic(fill):
                raise ValueError("fill observation conflict")
            changed = False
            for fee in fill.fees:
                changed = self._fee(fee) is not False or changed
            if fill.fee_final and not prior.fee_final:
                for component in tuple(self.unknown_components):
                    if component[:3] == key:
                        amount = self.unknown_components.pop(component)
                        self.unknown_fees[component[3]] = sub(
                            self.unknown_fees[component[3]], amount
                        )
                self.fills[key] = replace(prior, fee_final=True)
                self.reasons = ("VALUATION_REQUIRED",)
                changed = True
            return changed
        item = next(
            (
                r
                for r in self.reservations.values()
                if r.order_id == fill.order_id and r.status == "PENDING"
            ),
            None,
        )
        amount, principal = fill.quantity.amount, mul(fill.quantity.amount, fill.price.amount)
        risk = ZERO
        if item:
            if item.instrument != fill.instrument or item.side != fill.side:
                raise ValueError("reservation fill scope conflict")
            if amount > item.quantity.amount or (
                fill.side == "BUY" and principal > item.cash.amount
            ):
                raise ValueError("fill exceeds reservation")
            risk = proportional(item.risk.amount, amount, item.quantity.amount)
            self.reservations[item.reservation_id] = replace(
                item,
                quantity=Quantity(sub(item.quantity.amount, amount), item.quantity.base),
                cash=Money(
                    sub(item.cash.amount, principal) if fill.side == "BUY" else ZERO, self.quote
                ),
                risk=Money(sub(item.risk.amount, risk), self.quote),
            )
        debit = self.quote if fill.side == "BUY" else fill.instrument.base
        self._check_debit(debit, principal if fill.side == "BUY" else amount)
        sign = 1 if fill.side == "BUY" else -1
        self._post(
            fill.trade_id, fill.instrument.base, mul(amount, Decimal(sign)), "execution:base"
        )
        self._post(fill.trade_id, self.quote, mul(principal, Decimal(-sign)), "execution:quote")
        self.fills[key] = fill
        if fill.side == "BUY":
            self.lots.append(
                InventoryLot(
                    self._lot_id(fill),
                    fill.trade_id,
                    fill.instrument,
                    fill.quantity,
                    fill.event_time,
                    Money(principal, self.quote),
                    (),
                    Money(risk, self.quote),
                    origin_instrument=fill.instrument,
                )
            )
        else:
            self.lots = consume(self.lots, fill.instrument, amount, self._protected_inventory())
        if not fill.fee_final and (item is None or not item.fee_buffers):
            raise ValueError("unknown fee requires conservative reserved buffer")
        if item:
            # Convert only this fill's proportional fee allocation. The unfilled
            # buffer remains intact even if observed fees arrive or change later.
            current = self.reservations[item.reservation_id]
            buffers = []
            for buffer in current.fee_buffers:
                part = proportional(buffer.amount, amount, item.quantity.amount)
                if not fill.fee_final:
                    self.unknown_fees[buffer.currency] = exact_add(
                        self.unknown_fees.get(buffer.currency, ZERO), part
                    )
                    self.unknown_components[(*key, buffer.currency)] = part
                buffers.append(Money(sub(buffer.amount, part), buffer.currency))
            self.reservations[item.reservation_id] = replace(current, fee_buffers=tuple(buffers))
        for fee in fill.fees:
            self._fee(fee)
        self.reasons = ("VALUATION_REQUIRED",)

    def post_fee_correction(self, fee):
        return self._atomic("_fee", fee)

    def _fee(self, fee):
        key = (fee.account_id, fee.instrument, fee.fill_id)
        fill = self.fills.get(key)
        if fill is None or fee.revision < 1 or fee.value.amount < ZERO:
            raise ValueError("fee requires known scoped fill and nonnegative revision")
        identity = (*key, fee.value.currency)
        prior = self.fees.get(identity)
        if prior:
            if fee.revision < prior.revision:
                return False
            if fee.revision == prior.revision:
                if fee.value != prior.value:
                    raise ValueError("fee revision conflict")
                return False
        delta = sub(fee.value.amount, prior.value.amount if prior else ZERO)
        committed = self.unknown_components.pop(identity, ZERO)
        self.unknown_fees[fee.value.currency] = sub(
            self.unknown_fees.get(fee.value.currency, ZERO), committed
        )
        self._post(
            f"fee:{fee.fill_id}:{fee.revision}",
            fee.value.currency,
            delta.copy_negate(),
            "expense:fees",
        )
        self.fees[identity] = fee
        lots = []
        for lot in self.lots:
            if lot.lot_id == self._lot_id(fill):
                quantity = lot.quantity
                fees = tuple(f for f in lot.fees if f.value.currency != fee.value.currency) + (fee,)
                cost = lot.cost
                if fee.value.currency == self.quote:
                    base_fee = self.fees.get((*key, fill.instrument.base))
                    acquired = sub(
                        fill.quantity.amount, base_fee.value.amount if base_fee else ZERO
                    )
                    share = proportional(delta, lot.quantity.amount, acquired)
                    cost = Money(exact_add(cost.amount, share), self.quote)
                lot = replace(lot, quantity=quantity, cost=cost, fees=fees)
            lots.append(lot)
        self.lots = lots
        if fee.value.currency == fill.instrument.base:
            if fill.side == "BUY":
                # Holding instrument and originating fill differ for token rebates.
                # Apply a fee delta once across inventory owned by this exact fill.
                owned = [
                    lot
                    for lot in self.lots
                    if lot.origin_instrument == fill.instrument
                    and lot.fill_id == fee.fill_id
                    and lot.instrument == fill.instrument
                ]
                if delta > ZERO:
                    if total(lot.quantity.amount for lot in owned) < delta:
                        raise ValueError("available acquired lot insufficient for late base fee")
                    remaining = delta
                    for owned_lot in owned:
                        used = min(remaining, owned_lot.quantity.amount)
                        self.lots = [
                            replace(
                                lot,
                                quantity=Quantity(
                                    sub(lot.quantity.amount, used), lot.quantity.base
                                ),
                            )
                            if lot.lot_id == owned_lot.lot_id
                            else lot
                            for lot in self.lots
                        ]
                        remaining = sub(remaining, used)
                        if remaining == ZERO:
                            break
                elif delta < ZERO:
                    acquisition = next(
                        (lot for lot in owned if lot.lot_id == self._lot_id(fill)), None
                    )
                    if acquisition is not None:
                        self.lots = [
                            replace(
                                lot,
                                quantity=Quantity(
                                    sub(lot.quantity.amount, delta), lot.quantity.base
                                ),
                            )
                            if lot.lot_id == acquisition.lot_id
                            else lot
                            for lot in self.lots
                        ]
                    else:
                        self.lots.append(
                            InventoryLot(
                                self._lot_id(fill, f"fee:{fee.value.currency}:{fee.revision}"),
                                fee.fill_id,
                                fee.instrument,
                                Quantity(delta.copy_negate(), fee.instrument.base),
                                fill.event_time,
                                Money(ZERO, self.quote),
                                (fee,),
                                Money(ZERO, self.quote),
                                origin_instrument=fill.instrument,
                            )
                        )
            elif delta > ZERO:
                self.lots = consume(self.lots, fill.instrument, delta, self._protected_inventory())
            elif delta < ZERO:
                self.lots.append(
                    InventoryLot(
                        self._lot_id(fill, f"fee:{fee.value.currency}:{fee.revision}"),
                        fee.fill_id,
                        fee.instrument,
                        Quantity(delta.copy_negate(), fee.instrument.base),
                        fill.event_time,
                        Money(ZERO, self.quote),
                        (fee,),
                        Money(ZERO, self.quote),
                        origin_instrument=fill.instrument,
                    )
                )
        if fee.value.currency not in {fill.instrument.base, self.quote}:
            candidates = [lot for lot in self.lots if lot.instrument.base == fee.value.currency]
            if delta > ZERO:
                left = delta
                for lot in candidates:
                    used = min(left, lot.quantity.amount)
                    self.lots = consume(
                        self.lots, lot.instrument, used, self._protected_inventory()
                    )
                    left = sub(left, used)
                    if left == ZERO:
                        break
                if left:
                    raise ValueError("available fee currency inventory insufficient")
            elif delta < ZERO:
                instrument = next(
                    (
                        f.instrument
                        for f in self.fills.values()
                        if f.instrument.base == fee.value.currency
                    ),
                    None,
                )
                if instrument is None:
                    raise ValueError("fee rebate inventory instrument unknown")
                self.lots.append(
                    InventoryLot(
                        self._lot_id(fill, f"fee:{fee.value.currency}:{fee.revision}"),
                        fee.fill_id,
                        instrument,
                        Quantity(delta.copy_negate(), fee.value.currency),
                        fill.event_time,
                        Money(ZERO, self.quote),
                        (fee,),
                        Money(ZERO, self.quote),
                        origin_instrument=fill.instrument,
                    )
                )
        self.reasons = ("VALUATION_REQUIRED",)

    def reserve(self, reservation, expected_version):
        return self._atomic("_reserve", reservation, expected_version)

    def _reserve(self, item, version):
        if version != self.version or item.ledger_version != version:
            raise ValueError("stale ledger version")
        validate(item, self.account_id, self.quote)
        if item.reservation_id in self.reservations or any(
            r.order_id == item.order_id for r in self.reservations.values()
        ):
            raise ValueError("reservation identity conflict")
        for currency in {self.quote, item.instrument.base, *(m.currency for m in item.fee_buffers)}:
            amount = commitments((item,), currency)
            self._check_debit(currency, amount)
        self.reservations[item.reservation_id] = item

    def release(self, reservation_id, terminal_evidence):
        return self._atomic("_release", reservation_id, terminal_evidence)

    def _release(self, reservation_id, evidence):
        item = self.reservations[reservation_id]
        if (
            not evidence.terminal
            or evidence.status not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
            or evidence.account_id != self.account_id
            or evidence.order_id != item.order_id
            or evidence.instrument != item.instrument
            or evidence.cumulative_quantity.base != item.instrument.base
        ):
            raise ValueError("confirmed scoped terminal evidence required")
        filled = total(
            f.quantity.amount
            for f in self.fills.values()
            if f.order_id == item.order_id and f.instrument == item.instrument
        )
        if evidence.cumulative_quantity.amount != filled:
            raise ValueError("reconcile cumulative fills before release")
        if item.status == "RELEASED":
            return False
        self.reservations[reservation_id] = empty(item)

    def value(self, snapshot, market, cost_estimate):
        return self._atomic("_value", snapshot, market, cost_estimate)

    def _value(self, snapshot, market, cost_estimate):
        from .nav import value

        if snapshot != self.snapshot:
            raise ValueError("valuation snapshot version/state conflict")
        valued = value(snapshot, market, cost_estimate)
        self.nav, self.hwm, self.reasons = (
            valued.nav,
            valued.high_water_mark,
            valued.uncertainty_reasons,
        )

    def mark_dust(self, instrument, minimum):
        return self._atomic("_dust", instrument, minimum)

    def _dust(self, instrument, minimum):
        if minimum.base != instrument.base or minimum.amount <= ZERO:
            raise ValueError("positive legal minimum in instrument base required")
        amount = total(lot.quantity.amount for lot in self.lots if lot.instrument == instrument)
        self.lots = [
            replace(lot, dust=ZERO < amount < minimum.amount)
            if lot.instrument == instrument
            else lot
            for lot in self.lots
        ]

    @classmethod
    def replay(cls, account_id, quote, operations):
        book = cls(account_id, quote)
        for method, args in operations:
            book._atomic(method, *args)
        return book
