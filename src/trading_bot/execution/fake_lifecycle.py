"""Local synthetic venue only; its observation store is independent of the ledger."""

from trading_bot.domain.records import Fill
from trading_bot.execution.absence import (
    SyntheticAbsenceEvidence,
    SyntheticVenueOrder,
    SyntheticVenueSnapshot,
)
from trading_bot.execution.protection import command


class FakeLifecycleExchange:
    def __init__(self, source_id="synthetic:venue"):
        self.source_id = source_id
        self.sequence = 0
        self._orders = {}
        self._trades = {}

    def observe_trade(self, fill):
        key = (fill.account_id, fill.instrument.to_json(), fill.trade_id or fill.source_id)
        prior = self._trades.get(key)
        if prior is not None and prior != fill:
            raise ValueError("synthetic trade identity conflict")
        if prior is None:
            self.sequence += 1
            self._trades[key] = fill

    def observe_order(self, order):
        key = (order.account_id, order.instrument.to_json(), order.order_id)
        if self._orders.get(key) != order:
            self.sequence += 1
            self._orders[key] = order

    def snapshot(self, account_id, base, observed_at):
        # Complete scoped read of this fake's own order/trade store, never repository state.
        return SyntheticVenueSnapshot(
            self.source_id,
            account_id,
            base,
            self.sequence,
            observed_at,
            True,
            True,
            tuple(
                o
                for o in self._orders.values()
                if o.account_id == account_id and o.instrument.base == base
            ),
            tuple(
                f
                for f in self._trades.values()
                if f.account_id == account_id and f.instrument.base == base
            ),
        )

    def absence(self, account_id, instrument, ledger_version, quantity, observed_at):
        return SyntheticAbsenceEvidence(
            account_id,
            instrument,
            ledger_version,
            quantity,
            self.snapshot(account_id, instrument.base, observed_at),
        )

    def execute(self, repository, command_id, clock, *, absence=None):
        proposal, status = repository.get_command(command_id)
        if status != "PENDING":
            return False
        if absence is not None and absence.snapshot != self.snapshot(
            absence.account_id, absence.instrument.base, absence.snapshot.observed_at
        ):
            raise ValueError("synthetic venue snapshot generation changed")
        trigger = repository.get(proposal.instrument).context.trigger
        result = repository.execute_fake(command_id, clock, absence=absence)
        if result:
            self._record_effect(repository.account, proposal, trigger, clock.utc_now())
        return result

    def _record_effect(self, account, proposal, trigger, observed_at):
        # Record only successfully simulated command effects, never infer venue state
        # from the local projection or a cancel acknowledgement's eventual outcome.
        def order(order_id, status):
            self.observe_order(
                SyntheticVenueOrder(account, proposal.instrument, order_id, "SELL", status)
            )

        kind = proposal.kind
        if kind == "CREATE_STOP":
            order(proposal.command_id, "ACTIVE")
            return
        if kind == "CANCEL_STOP":
            key = (account, proposal.instrument.to_json(), proposal.stop_id)
            prior = self._orders.get(key)
            if prior is None or prior.status not in {"CANCELED", "FILLED", "REJECTED", "EXPIRED"}:
                order(proposal.stop_id, "CANCEL_PENDING")
            return
        if kind in {"NATIVE_REPLACE_STOP", "NATIVE_EMERGENCY_EXIT"}:
            order(proposal.stop_id, "CANCELED")
            child_kind = "CREATE_STOP" if kind == "NATIVE_REPLACE_STOP" else "MARKET_SELL"
            child = command(
                "synthetic:atomic:" + proposal.command_id,
                child_kind,
                proposal.instrument,
                proposal.quantity.amount,
                trigger=trigger if child_kind == "CREATE_STOP" else None,
            )
            order_id = child.command_id
            if kind == "NATIVE_REPLACE_STOP":
                order(order_id, "ACTIVE")
                return
        elif kind == "NATIVE_LINKED_EXIT":
            order_id = proposal.stop_id
        else:
            order_id = proposal.command_id
        order(order_id, "FILLED")
        self.observe_trade(
            Fill(
                "synthetic:" + proposal.command_id,
                account,
                proposal.instrument,
                order_id,
                "SELL",
                proposal.quantity,
                trigger,
                (),
                observed_at,
                "synthetic:" + proposal.command_id,
                True,
            )
        )
