"""Telegram approval flow for PAPER TRADES ONLY - never a real order, never IBKR.

Flow:
  1. main.py sends the daily report, then one extra message per Top Candidate
     (from report_writer.select_top_candidates - never High Risk Dip Watchlist
     entries, which are informational only and never get buttons at all) with
     "Approve Paper Trade" / "Reject" / "Watch Only" buttons, and saves a PENDING
     record here so a later button press can be resolved back to that candidate's
     full trade detail.
  2. approval_listener.py runs continuously (separately from the daily batch job)
     and polls Telegram for button presses. When one arrives, process_decision()
     re-validates it and, only on "approve", appends a row to paper_trades.csv.

Why "in Top Candidates" doesn't need re-checking at approval time: a pending
record only ever gets created from select_top_candidates()'s output at send time,
so "was in Top Candidates" is already guaranteed by construction, and there is no
fresher "Top Candidates" state to compare against until tomorrow's run recomputes
everything. aggressive_mode.enabled is the one thing that legitimately CAN change
between send time and click time (a config edit), so process_decision() re-checks
that specific flag live, at approval time, not just whatever it was when the
candidate was sent.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .strategies.mean_reversion import STRATEGY_NAME_AGGRESSIVE, STRATEGY_NAME_SAFE
from .utils import resolve_path

CALLBACK_PREFIX = "pt"
VALID_ACTIONS = {"approve", "reject", "watch"}
_DECISION_STATUS = {"approve": "APPROVED", "reject": "REJECTED", "watch": "WATCH_ONLY"}

# paper_trades.csv schema. A row is created here (status=OPEN) the moment a
# candidate is approved, then updated in place by paper_trade_tracker.py as it
# moves toward TARGET_HIT / STOPPED / TIME_EXIT. See paper_trade_tracker.py's
# module docstring for the full lifecycle and its same-bar conservative rule.
PAPER_TRADE_COLUMNS = [
    "trade_id", "approved_at", "ticker", "strategy", "mode", "signal", "score",
    "entry_price", "stop_loss", "target_price", "risk_reward", "position_size",
    "risk_amount", "status", "opened_at", "exit_price", "exited_at", "exit_reason",
    "pnl_dollars", "pnl_pct", "holding_days", "notes",
]


# --- callback_data encoding (Telegram caps this at 64 bytes) ----------------------


def encode_callback_data(action: str, symbol: str, report_date: str) -> str:
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unknown action: {action!r} (expected one of {sorted(VALID_ACTIONS)})")
    data = f"{CALLBACK_PREFIX}:{action}:{symbol}:{report_date}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError(f"callback_data exceeds Telegram's 64-byte limit: {data!r}")
    return data


def decode_callback_data(data: str) -> tuple[str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != CALLBACK_PREFIX or parts[1] not in VALID_ACTIONS:
        return None
    _, action, symbol, report_date = parts
    return action, symbol, report_date


def build_approval_keyboard(symbol: str, report_date: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "✅ Approve Paper Trade", "callback_data": encode_callback_data("approve", symbol, report_date)}],
            [
                {"text": "❌ Reject", "callback_data": encode_callback_data("reject", symbol, report_date)},
                {"text": "👀 Watch Only", "callback_data": encode_callback_data("watch", symbol, report_date)},
            ],
        ]
    }


def _final_position(entry: dict[str, Any]) -> dict[str, Any]:
    """The portfolio-/regime-adjusted position when that pipeline has run
    (entry["portfolio_evaluation"]["position"]), falling back to the raw
    individual-risk result otherwise. Duplicated as a small inline expression
    rather than imported from report_writer.py, which itself imports
    performance_tracker -> paper_trades - importing report_writer here would
    close that into a cycle."""
    portfolio_eval = entry.get("portfolio_evaluation")
    if portfolio_eval is not None and portfolio_eval.get("position") is not None:
        return portfolio_eval["position"]
    return entry["best_risk_result"]


def format_approval_message(entry: dict[str, Any]) -> str:
    risk = _final_position(entry)
    is_aggressive = risk["strategy"] == STRATEGY_NAME_AGGRESSIVE
    tag = " [AGGRESSIVE]" if is_aggressive else ""
    return (
        f"Paper Trade Approval{tag}\n\n"
        f"{entry['symbol']}\n"
        f"Signal: {entry['label']} (Score {entry['score']}/100)\n"
        f"Strategy: {risk['strategy']}\n"
        f"Entry: {risk['entry']}\n"
        f"Target: {risk['target']}\n"
        f"Stop loss: {risk['stop_loss']}\n"
        f"Risk/reward: {risk['risk_reward']}\n"
        f"Suggested size: {risk['shares']} shares (~${risk['dollar_risk']:.0f} at risk)\n\n"
        f"Tap a button below. This only ever records a PAPER trade - no real order is placed."
    )


# --- pending-approval store (JSON, keyed by "report_date|symbol") -----------------


def _key(symbol: str, report_date: str) -> str:
    return f"{report_date}|{symbol}"


def _pending_path(config: dict[str, Any]) -> Path:
    return resolve_path(config["data"]["journal_dir"]) / config["paper_trading"]["pending_approvals_file"]


def _prune_expired(records: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    expiry_hours = config["paper_trading"]["pending_expiry_hours"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=expiry_hours)
    return {
        key: record
        for key, record in records.items()
        if datetime.fromisoformat(record["created_at"]) >= cutoff
    }


def load_pending_approvals(config: dict[str, Any]) -> dict[str, Any]:
    path = _pending_path(config)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)
    return _prune_expired(records, config)


def _save_all(records: dict[str, Any], config: dict[str, Any]) -> None:
    path = _pending_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


def pending_record_from_entry(
    entry: dict[str, Any], report_date: str, message_id: int, chat_id: str
) -> dict[str, Any]:
    risk = _final_position(entry)
    return {
        "report_date": report_date,
        "symbol": entry["symbol"],
        "chat_id": str(chat_id),
        "message_id": message_id,
        "strategy": risk["strategy"],
        "is_aggressive": risk["strategy"] == STRATEGY_NAME_AGGRESSIVE,
        "signal": entry["label"],
        "score": entry["score"],
        "entry": risk["entry"],
        "stop_loss": risk["stop_loss"],
        "target": risk["target"],
        "risk_reward": risk["risk_reward"],
        "expected_upside_pct": risk["expected_upside_pct"],
        "expected_downside_pct": risk["expected_downside_pct"],
        "shares": risk["shares"],
        "dollar_risk": risk["dollar_risk"],
        "status": "PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decided_at": None,
    }


def save_pending_approval(record: dict[str, Any], config: dict[str, Any]) -> None:
    records = load_pending_approvals(config)
    records[_key(record["symbol"], record["report_date"])] = record
    _save_all(records, config)


# --- decision processing (called by approval_listener.py on each button press) ---


def process_decision(
    action: str, symbol: str, report_date: str, config: dict[str, Any], logger: logging.Logger
) -> tuple[bool, str]:
    """Resolve one button press. Returns (success, message_to_show_the_user).

    `success=True` means the decision was recorded (approved/rejected/watch-only);
    it does NOT by itself mean a paper trade was written - only "approve" ever
    writes to paper_trades.csv, and even then only after the aggressive-mode
    re-check below passes.
    """
    if action not in VALID_ACTIONS:
        return False, f"Unrecognized action: {action}"

    key = _key(symbol, report_date)
    records = load_pending_approvals(config)
    record = records.get(key)

    if record is None:
        return False, f"No pending approval found for {symbol} on {report_date} (expired or unknown)."

    if record["status"] != "PENDING":
        return False, f"{symbol} was already marked {record['status']}."

    if action == "approve" and record["is_aggressive"]:
        # Re-check NOW, not whatever it was when the message was sent - the flag
        # could have been switched off in the meantime, and an Aggressive
        # candidate must never be approvable while it's disabled.
        currently_enabled = config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"]
        if not currently_enabled:
            record["status"] = "BLOCKED_AGGRESSIVE_DISABLED"
            record["decided_at"] = datetime.now(timezone.utc).isoformat()
            records[key] = record
            _save_all(records, config)
            logger.warning("Blocked aggressive-mode approval for %s: aggressive_mode.enabled is false.", symbol)
            return False, (
                f"{symbol} is an Aggressive mean-reversion candidate and aggressive_mode.enabled "
                f"is currently false - it cannot be approved as a paper trade."
            )

    record["status"] = _DECISION_STATUS[action]
    record["decided_at"] = datetime.now(timezone.utc).isoformat()
    records[key] = record
    _save_all(records, config)

    if action == "approve":
        record_paper_trade(record, config)
        logger.info("Approved paper trade: %s (%s)", symbol, record["strategy"])
        return True, f"{symbol} approved and recorded in paper_trades.csv."

    if action == "reject":
        return True, f"{symbol} rejected - no paper trade recorded."

    return True, f"{symbol} set to watch only - no paper trade recorded."


def _mode_for_strategy(strategy_name: str) -> str:
    """Safe/Aggressive only means something for Mean Reversion - every other
    strategy (Momentum Breakout, Trend Following) has no such split."""
    if strategy_name == STRATEGY_NAME_SAFE:
        return "Safe"
    if strategy_name == STRATEGY_NAME_AGGRESSIVE:
        return "Aggressive"
    return "N/A"


def generate_trade_id(symbol: str, report_date: str) -> str:
    return f"{symbol}_{report_date}_{uuid.uuid4().hex[:8]}"


def _paper_trades_path(config: dict[str, Any]) -> Path:
    journal_dir = resolve_path(config["data"]["journal_dir"])
    journal_dir.mkdir(parents=True, exist_ok=True)
    return journal_dir / config["paper_trading"]["paper_trades_file"]


def load_paper_trades_df(config: dict[str, Any]) -> pd.DataFrame:
    """The single read path for paper_trades.csv - used both here (to append a
    new OPEN row) and by paper_trade_tracker.py / performance_tracker.py (to
    read/update existing rows). Always returns the full expected column set,
    even for a brand-new, empty file, so callers never have to special-case it."""
    path = _paper_trades_path(config)
    if not path.exists():
        return pd.DataFrame(columns=PAPER_TRADE_COLUMNS)
    return pd.read_csv(path, dtype={"trade_id": str})


def save_paper_trades_df(df: pd.DataFrame, config: dict[str, Any]) -> Path:
    """The single write path for paper_trades.csv - always a full rewrite of the
    whole file from the given DataFrame (not an append), so a caller that loaded,
    mutated a row, and calls this back is doing a clean read-modify-write, not a
    duplicate-append."""
    path = _paper_trades_path(config)
    df.to_csv(path, index=False)
    return path


def record_paper_trade(record: dict[str, Any], config: dict[str, Any]) -> Path:
    """Create one OPEN paper position from an approved candidate.

    Default status is OPEN immediately - there is no separate PENDING state for
    the *position* itself. (PENDING already means something else here: it's the
    status of the pending-approval *request* in pending_approvals.json before a
    button is tapped. By the time this function runs, Approve has already been
    tapped, so the resulting position starts life OPEN - the paper-trade
    equivalent of "this would already be live.")

    Version 1 never executes trades, paper or real, beyond this ledger entry.
    exit_price/exited_at/exit_reason/pnl_dollars/pnl_pct/holding_days start blank
    and are filled in later by paper_trade_tracker.py as the position resolves.
    """
    trade = {
        "trade_id": generate_trade_id(record["symbol"], record["report_date"]),
        "approved_at": record.get("decided_at"),
        "ticker": record["symbol"],
        "strategy": record["strategy"],
        "mode": _mode_for_strategy(record["strategy"]),
        "signal": record["signal"],
        "score": record["score"],
        "entry_price": record["entry"],
        "stop_loss": record["stop_loss"],
        "target_price": record["target"],
        "risk_reward": record["risk_reward"],
        "position_size": record["shares"],
        "risk_amount": record["dollar_risk"],
        "status": "OPEN",
        "opened_at": record["report_date"],
        "exit_price": "",
        "exited_at": "",
        "exit_reason": "",
        "pnl_dollars": "",
        "pnl_pct": "",
        "holding_days": "",
        "notes": "",
    }

    existing = load_paper_trades_df(config)
    new_row_df = pd.DataFrame([trade])
    combined = pd.concat([existing, new_row_df], ignore_index=True) if not existing.empty else new_row_df
    return save_paper_trades_df(combined, config)
