"""Order submission, bracket protection, partial-fill handling, and
idempotency (Phase 7 Parts F/G/H/I).

**Bracket safety decision (Part F):** rather than relying on IBKR's
parent/child "transmit" semantics (where a mis-ordered transmit sequence
could submit a naked entry or a naked exit), this module submits the ENTRY
order first, waits for a CONFIRMED fill, and only then submits the
protective STOP and target LIMIT as its exit children, sized to the ACTUAL
filled quantity - never the originally requested quantity. This is the
"choose the safer implementation and document it" path Part F explicitly
allows when bracket transmission semantics can't be proven safe from this
codebase alone (no IBKR is reachable to test against in this environment -
see `IBKRClient`'s module docstring). The tradeoff: a brief window after
entry fills where the position is unprotected until the exit legs are
placed; `order_manager.py` places them synchronously, immediately upon
detecting the fill, to keep that window as short as possible.

**Broker-enforced OCA (GitHub Issue #1 P0):** the stop and target legs are
still two independent SELL orders (not an IBKR native bracket), but both
carry the SAME `ocaGroup`/`ocaType` (see `_exit_intent()` and
`IBKRClient._build_ibkr_order()`) - TWS itself cancels one the instant the
other fills or is cancelled. This closes the race the earlier design left
open: between a fill and this process's own poll-interval-bound local
sibling-cancel (`learning_feedback.check_exit_fills()`), both legs were
technically still live at the broker and could both have filled (e.g. a
fast-moving gap through both levels). The local cancel in `learning_
feedback.py` stays in place as defense-in-depth, not a replacement.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import order_state
from .broker import Broker, BrokerOrder, BrokerOrderRejected
from .order_state import (
    STATE_ACKNOWLEDGED,
    STATE_CANCELLED,
    STATE_CLOSED,
    STATE_CREATED,
    STATE_ERROR,
    STATE_EXIT_PENDING,
    STATE_FILLED,
    STATE_PARTIALLY_FILLED,
    STATE_REJECTED,
    STATE_SUBMITTED,
    OrderIntent,
)


@dataclass
class ManagedOrder:
    """Everything `order_manager.py` tracks for one intent's whole lifecycle
    - persisted via `ExecutionJournal` (Part U) after every state change."""

    intent: OrderIntent
    state: str = STATE_CREATED
    entry_broker_order_id: str | None = None
    stop_broker_order_id: str | None = None
    target_broker_order_id: str | None = None
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["intent"] = asdict(self.intent)
        data["intent"]["created_at"] = self.intent.created_at.isoformat()
        return data


class DuplicateIntentError(Exception):
    pass


def _intent_from_dict(data: dict[str, Any]) -> OrderIntent:
    """Inverse of `ExecutionJournal.record_state()`'s `intent` payload -
    reconstructs the exact `OrderIntent` a journal row's ManagedOrder was
    submitted with, so a restarted process can resume managing it (poll
    fills, sync protection) rather than only knowing "a trade_id exists"."""
    payload = dict(data)
    payload["created_at"] = datetime.fromisoformat(payload["created_at"])
    return OrderIntent(**payload)


class OrderManager:
    """Owns the CREATED -> SUBMITTED -> ... -> CLOSED lifecycle for every
    intent this process has ever submitted, and is the ONLY thing in this
    codebase allowed to call `broker.submit_order()`."""

    def __init__(self, broker: Broker, config: dict[str, Any], journal: "ExecutionJournal | None" = None, on_event: Any = None):
        self.broker = broker
        self.config = config
        self.journal = journal
        # Optional `(event: str, managed: ManagedOrder, extra: dict) -> None`
        # hook, called every time `_record()` journals a state transition -
        # lets a caller (position_monitor.py, main.py's auto-execution)
        # turn "rejected"/"fill"/"protection_synced" into a Telegram notice
        # without order_manager.py itself knowing anything about Telegram.
        # A raising callback is swallowed here on purpose: a notification
        # failure must never be allowed to break order management, which
        # is why `_record()` still journals the transition FIRST either way.
        self.on_event = on_event
        self._managed: dict[str, ManagedOrder] = {}  # keyed by intent_id
        self._trade_id_index: dict[str, str] = {}  # trade_id -> intent_id, for idempotency

    def is_duplicate(self, intent: OrderIntent) -> bool:
        """Idempotency check (Part I): same trade_id already has a managed
        order in a non-terminal state, OR an identical (ticker, strategy,
        entry/stop/target) intent was already submitted today.

        After a process restart, `self._managed` starts empty and only
        `self._trade_id_index` is rehydrated (via
        `restore_from_journal_rows`) - so a trade_id present in the index
        may have no corresponding entry in `self._managed` yet. That index
        only ever contains trade_ids whose LAST recorded journal state was
        already non-terminal, so its mere presence is itself sufficient
        proof of an active duplicate; there is no live `ManagedOrder` to
        fall back to querying in that case, and there does not need to be."""
        if intent.trade_id and intent.trade_id in self._trade_id_index:
            existing = self._managed.get(self._trade_id_index[intent.trade_id])
            if existing is None:
                return True
            if existing.state not in (STATE_CANCELLED, STATE_REJECTED, STATE_CLOSED, STATE_ERROR):
                return True
        for existing in self._managed.values():
            if existing.state in (STATE_CANCELLED, STATE_REJECTED, STATE_CLOSED, STATE_ERROR):
                continue
            if (
                existing.intent.ticker == intent.ticker
                and existing.intent.strategy == intent.strategy
                and abs(existing.intent.entry_price - intent.entry_price) < 1e-6
                and abs(existing.intent.stop_loss - intent.stop_loss) < 1e-6
            ):
                return True
        return False

    def restore_from_journal_rows(self, rows: list[dict[str, Any]]) -> None:
        """Rehydrate in-memory state from a persisted journal - called once
        at process startup before any new submission is attempted, and the
        ONLY thing that makes restart recovery actually work (Part I/J):

        - Duplicate prevention (`is_duplicate()`'s trade_id index), as
          before.
        - A full `ManagedOrder` rebuilt into `self._managed` for every
          intent_id whose last recorded state is non-terminal - this is
          what lets a restarted `position_monitor` keep polling fills and
          syncing stop/target protection for an order a DIFFERENT (now-
          dead) process submitted, instead of silently losing track of it
          until the next full reconciliation discrepancy. Without this, a
          process that dies with an open entry order, a partial fill, or
          active protection orphans all of it on restart.

        The journal is append-only, so the LAST "state" row for a given
        intent_id is authoritative; earlier rows for the same intent_id are
        superseded, not merged."""
        latest_by_intent: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.get("event") != "state" or not row.get("intent_id"):
                continue
            latest_by_intent[row["intent_id"]] = row

        for intent_id, row in latest_by_intent.items():
            state = row.get("state")
            trade_id = row.get("trade_id")
            if trade_id and state not in (STATE_CANCELLED, STATE_REJECTED, STATE_CLOSED, STATE_ERROR):
                self._trade_id_index[trade_id] = intent_id

            if state in (STATE_CANCELLED, STATE_REJECTED, STATE_CLOSED, STATE_ERROR):
                continue  # terminal - nothing left to manage; the trade_id index entry above is skipped too

            intent_data = row.get("intent")
            if intent_data is None:
                continue  # an older journal row written before intents were persisted in full - duplicate index above still works, there's just nothing to rebuild

            try:
                intent = _intent_from_dict(intent_data)
            except (TypeError, ValueError, KeyError):
                continue  # fail closed on a corrupt row - never guess an intent back into existence

            self._managed[intent_id] = ManagedOrder(
                intent=intent,
                state=state or STATE_CREATED,
                entry_broker_order_id=row.get("entry_broker_order_id"),
                stop_broker_order_id=row.get("stop_broker_order_id"),
                target_broker_order_id=row.get("target_broker_order_id"),
                filled_quantity=row.get("filled_quantity") or 0.0,
                avg_fill_price=row.get("avg_fill_price"),
                rejection_reason=row.get("rejection_reason"),
            )

    def submit_entry(self, intent: OrderIntent, allowed_tickers: set[str] | None = None) -> ManagedOrder:
        errors = order_state.validate_intent(intent, self.config, allowed_tickers)
        if errors:
            managed = ManagedOrder(intent=intent, state=STATE_ERROR, rejection_reason="; ".join(errors))
            self._record(managed, event="validation_failed")
            return managed

        if self.is_duplicate(intent):
            managed = ManagedOrder(intent=intent, state=STATE_ERROR, rejection_reason="Duplicate intent - an equivalent order is already active.")
            self._record(managed, event="duplicate_blocked")
            return managed

        managed = ManagedOrder(intent=intent, state=STATE_SUBMITTED)
        self._managed[intent.intent_id] = managed
        if intent.trade_id:
            self._trade_id_index[intent.trade_id] = intent.intent_id
        self._record(managed, event="submitting")

        try:
            broker_order = self.broker.submit_order(intent)
        except BrokerOrderRejected as exc:
            # The broker explicitly said no (e.g. a TWS percentage-
            # constraint rejection) - distinct from STATE_ERROR below so
            # callers/journal readers can tell "broker refused" apart from
            # "we don't actually know what happened".
            managed.state = STATE_REJECTED
            managed.rejection_reason = str(exc)
            self._record(managed, event="rejected")
            return managed
        except Exception as exc:  # noqa: BLE001 - connection failure, timeout, or any other submission error
            managed.state = STATE_ERROR
            managed.rejection_reason = str(exc)
            self._record(managed, event="submission_error")
            return managed

        managed.entry_broker_order_id = broker_order.broker_order_id
        managed.state = STATE_ACKNOWLEDGED
        self._record(managed, event="acknowledged")
        return managed

    def poll_entry_fill(self, intent_id: str) -> ManagedOrder:
        """Checks the broker for fill progress on one managed order's entry
        leg and, on any new fill, submits/updates protective exit orders
        sized to the ACTUAL filled quantity (Part H: never the originally
        requested quantity).

        Uses `broker.get_order()` - which returns an order regardless of
        terminal state - rather than `broker.open_orders()`, which by
        definition never includes a Rejected (or Cancelled/Filled) order.
        Looking a rejection up through `open_orders()` alone would make
        that branch unreachable dead code: the order is filtered out of
        the very collection being searched before the status check ever
        runs."""
        managed = self._managed[intent_id]
        if managed.entry_broker_order_id is None:
            return managed

        broker_order = self.broker.get_order(managed.entry_broker_order_id)
        if broker_order is None:
            # Broker has no record of this order id at all - nothing to do.
            return managed

        if broker_order.status == "Rejected":
            managed.state = STATE_REJECTED
            managed.rejection_reason = "Broker rejected the entry order."
            self._record(managed, event="rejected")
            return managed

        if broker_order.filled_quantity > managed.filled_quantity:
            self._apply_fill(managed, broker_order.filled_quantity, broker_order.avg_fill_price)
        elif managed.filled_quantity > 0 and (managed.stop_broker_order_id is None or managed.target_broker_order_id is None):
            # Recovery path: a genuinely filled position with no NEW fill
            # to report can still be missing its protection - e.g. the
            # process died between _apply_fill()'s "fill" journal row and
            # _sync_protection() actually placing the stop/target leg(s),
            # or a restart rebuilt this ManagedOrder from a journal row
            # recorded at that exact gap. Waiting for a NEW fill would
            # never catch this if the position is already fully filled -
            # so every poll re-checks protection completeness directly.
            self._sync_protection(managed)

        return managed

    def _apply_fill(self, managed: ManagedOrder, total_filled_quantity: float, avg_price: float | None) -> None:
        previously_filled = managed.filled_quantity
        managed.filled_quantity = total_filled_quantity
        managed.avg_fill_price = avg_price
        newly_filled = total_filled_quantity - previously_filled

        if total_filled_quantity >= managed.intent.quantity:
            managed.state = STATE_FILLED
        else:
            managed.state = STATE_PARTIALLY_FILLED
        self._record(managed, event="fill", newly_filled=newly_filled)

        self._sync_protection(managed)

    def _sync_protection(self, managed: ManagedOrder) -> None:
        """Places (or replaces) the protective STOP and target LIMIT so
        their quantity always matches the CURRENT filled quantity - never
        the full requested size (Part H's core requirement)."""
        protect_qty = managed.filled_quantity
        if protect_qty <= 0:
            return

        just_created = managed.stop_broker_order_id is None and managed.target_broker_order_id is None

        stop_intent = _exit_intent(managed.intent, order_state.ORDER_TYPE_STOP, managed.intent.stop_loss, protect_qty)
        target_intent = _exit_intent(managed.intent, order_state.ORDER_TYPE_LIMIT, managed.intent.target_price, protect_qty)

        if managed.stop_broker_order_id is None:
            managed.stop_broker_order_id = self.broker.submit_order(stop_intent).broker_order_id
        else:
            self.broker.replace_order(managed.stop_broker_order_id, quantity=protect_qty)

        if managed.target_broker_order_id is None:
            managed.target_broker_order_id = self.broker.submit_order(target_intent).broker_order_id
        else:
            self.broker.replace_order(managed.target_broker_order_id, quantity=protect_qty)

        managed.state = STATE_EXIT_PENDING if managed.state == STATE_FILLED else managed.state
        self._record(managed, event="protection_synced", protected_quantity=protect_qty, _protection_just_created=just_created)

    def cancel_entry(self, intent_id: str) -> bool:
        managed = self._managed[intent_id]
        if managed.entry_broker_order_id is None:
            return False
        ok = self.broker.cancel_order(managed.entry_broker_order_id)
        if ok:
            managed.state = STATE_CANCELLED
            self._record(managed, event="cancelled")
        return ok

    def close_position(self, intent_id: str) -> None:
        managed = self._managed[intent_id]
        managed.state = STATE_CLOSED
        self._record(managed, event="closed")

    def get(self, intent_id: str) -> ManagedOrder | None:
        return self._managed.get(intent_id)

    def all_managed(self) -> list[ManagedOrder]:
        return list(self._managed.values())

    def _record(self, managed: ManagedOrder, event: str, **extra: Any) -> None:
        if self.journal is not None:
            self.journal.record_state(managed, event=event, extra=extra)
        if self.on_event is not None:
            try:
                self.on_event(event, managed, extra)
            except Exception:  # noqa: BLE001 - a notification failure must never break order management; the state above is already journaled regardless
                pass


def oca_group_for_entry(entry_intent_id: str) -> str:
    """Stable OCA group name for one entry's protective exit pair - the
    SAME value every time `_sync_protection()` is called for this managed
    order, including across a later resize (`replace_order()` preserves
    whatever `oca_group` TWS already echoed back - see `ibkr_client.py`),
    so the stop and target legs stay linked at the broker for the whole
    life of the position, not just at creation."""
    return f"oca_{entry_intent_id}"


def _exit_intent(entry_intent: OrderIntent, order_type: str, price: float, quantity: float) -> OrderIntent:
    return order_state.OrderIntent(
        intent_id=order_state.new_intent_id(),
        ticker=entry_intent.ticker,
        side=order_state.SIDE_SELL,
        quantity=int(quantity),
        order_type=order_type,
        entry_price=price,
        stop_loss=entry_intent.stop_loss,
        target_price=entry_intent.target_price,
        strategy=entry_intent.strategy,
        signal_score=entry_intent.signal_score,
        quant_score=entry_intent.quant_score,
        risk_amount=entry_intent.risk_amount,
        created_at=entry_intent.created_at,
        trade_id=entry_intent.trade_id,
        regime=entry_intent.regime,
        account_mode_at_creation=entry_intent.account_mode_at_creation,
        # Broker-enforced OCA linkage (GitHub Issue #1 P0) - IBKRClient.
        # submit_order()/replace_order() read oca_group/oca_type from here
        # and set them on the real ibapi Order; FakeBroker ignores them
        # (it has no server-side OCA concept to simulate), so this is a
        # pure addition, not a behavior change, for every existing test.
        metadata={"parent_intent_id": entry_intent.intent_id, "oca_group": oca_group_for_entry(entry_intent.intent_id), "oca_type": 1},
    )


class ExecutionJournal:
    """Durable, append-only record of every state change (Phase 7 Part U).
    JSON Lines under `data/journal/executions.jsonl` by default - one line
    per event, never rewritten, so a corrupt/partial last line can never
    lose earlier history. Never logs credentials (there are none in an
    `OrderIntent`/`ManagedOrder` to begin with)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_state(self, managed: ManagedOrder, event: str, extra: dict[str, Any] | None = None) -> None:
        intent_dict = asdict(managed.intent)
        intent_dict["created_at"] = managed.intent.created_at.isoformat()
        row = {
            "event": "state",
            "type": event,
            "intent_id": managed.intent.intent_id,
            "trade_id": managed.intent.trade_id,
            "ticker": managed.intent.ticker,
            "state": managed.state,
            "entry_broker_order_id": managed.entry_broker_order_id,
            "stop_broker_order_id": managed.stop_broker_order_id,
            "target_broker_order_id": managed.target_broker_order_id,
            "filled_quantity": managed.filled_quantity,
            "avg_fill_price": managed.avg_fill_price,
            "rejection_reason": managed.rejection_reason,
            # The full OrderIntent, so restore_from_journal_rows() can
            # rebuild a complete ManagedOrder after a restart - see its
            # docstring. Everything here is plain JSON-safe data (no
            # credentials, no broker objects).
            "intent": intent_dict,
        }
        if extra:
            row.update(extra)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
