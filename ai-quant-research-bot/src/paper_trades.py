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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .strategies.mean_reversion import STRATEGY_NAME_AGGRESSIVE
from .utils import resolve_path

CALLBACK_PREFIX = "pt"
VALID_ACTIONS = {"approve", "reject", "watch"}
_DECISION_STATUS = {"approve": "APPROVED", "reject": "REJECTED", "watch": "WATCH_ONLY"}


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


def format_approval_message(entry: dict[str, Any]) -> str:
    risk = entry["best_risk_result"]
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
    risk = entry["best_risk_result"]
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


def record_paper_trade(record: dict[str, Any], config: dict[str, Any]) -> Path:
    """Append one approved candidate to data/journal/paper_trades.csv.

    Version 1 never executes trades, paper or real, beyond this ledger entry:
    `status` starts at OPEN and exit_price/pnl are left blank for you to fill in
    manually as you track the (paper) outcome, same convention as trade_journal.csv.
    """
    journal_dir = resolve_path(config["data"]["journal_dir"])
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / config["paper_trading"]["paper_trades_file"]

    row = {
        "approved_at": record.get("decided_at"),
        "alert_date": record["report_date"],
        "symbol": record["symbol"],
        "strategy": record["strategy"],
        "is_aggressive": record["is_aggressive"],
        "signal": record["signal"],
        "score": record["score"],
        "entry": record["entry"],
        "stop_loss": record["stop_loss"],
        "target": record["target"],
        "risk_reward": record["risk_reward"],
        "expected_upside_pct": record["expected_upside_pct"],
        "expected_downside_pct": record["expected_downside_pct"],
        "shares": record["shares"],
        "dollar_risk": record["dollar_risk"],
        "status": "OPEN",
        "exit_price": "",
        "pnl": "",
        "notes": "",
    }

    new_df = pd.DataFrame([row])
    if path.exists():
        existing = pd.read_csv(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_csv(path, index=False)
    return path
