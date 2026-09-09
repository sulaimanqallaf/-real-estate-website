"""Format the daily Telegram report text and persist CSV/JSON snapshots."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .utils import resolve_path


def _fmt(value: Any, suffix: str = "", digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and value != value):  # NaN check
        return "N/A"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return str(value)


def format_asset_block(entry: dict[str, Any]) -> str:
    plan = entry["trade_plan"]
    if plan["tradeable"]:
        entry_str = f"{plan['entry_low']}-{plan['entry_high']}"
        target_str = _fmt(plan["target"])
        stop_str = _fmt(plan["stop_loss"])
        upside_str = _fmt(plan["expected_upside_pct"], suffix="%")
        downside_str = _fmt(plan["expected_downside_pct"], suffix="%")
        rr_str = _fmt(plan["risk_reward"])
    else:
        entry_str = target_str = stop_str = upside_str = downside_str = rr_str = "No trade recommended"

    return (
        f"{entry['symbol']}\n"
        f"Signal: {entry['signal']}\n"
        f"Score: {entry['score']}/100\n"
        f"Price: {_fmt(entry['snapshot']['close'])}\n"
        f"Trend: {entry['trend_status']}\n"
        f"Momentum: {entry['momentum_status']}\n"
        f"RSI: {_fmt(entry['snapshot']['rsi_14'], digits=1)}\n"
        f"Suggested entry: {entry_str}\n"
        f"Target: {target_str}\n"
        f"Stop loss: {stop_str}\n"
        f"Expected upside: {upside_str}\n"
        f"Expected downside: {downside_str}\n"
        f"Risk/reward: {rr_str}\n"
        f"Reason: {entry['explanation']}"
    )


def format_report_text(ranked_entries: list[dict[str, Any]], report_date: str, failed_symbols: list[str]) -> str:
    header = f"Daily AI Quant Report\n{report_date}\n(Data collection & analysis only — no trades executed)"
    blocks = [format_asset_block(entry) for entry in ranked_entries]
    body = "\n\n".join(blocks) if blocks else "No assets could be analyzed today."

    footer = ""
    if failed_symbols:
        footer = "\n\nSkipped (data error): " + ", ".join(failed_symbols)

    return f"{header}\n\n{body}{footer}"


def _entry_to_flat_row(entry: dict[str, Any]) -> dict[str, Any]:
    plan = entry["trade_plan"]
    snap = entry["snapshot"]
    return {
        "symbol": entry["symbol"],
        "rank": entry["rank"],
        "signal": entry["signal"],
        "score": entry["score"],
        "price": snap["close"],
        "trend_status": entry["trend_status"],
        "momentum_status": entry["momentum_status"],
        "rsi_14": snap["rsi_14"],
        "atr_14": snap["atr_14"],
        "momentum_20d_pct": snap["momentum_20d"],
        "momentum_60d_pct": snap["momentum_60d"],
        "sma_50": snap["sma_50"],
        "sma_200": snap["sma_200"],
        "volume_ratio_20d": snap["volume_ratio_20d"],
        "daily_volatility_pct": snap["daily_volatility_pct"],
        "tradeable": plan["tradeable"],
        "entry_low": plan["entry_low"],
        "entry_high": plan["entry_high"],
        "target": plan["target"],
        "stop_loss": plan["stop_loss"],
        "expected_upside_pct": plan["expected_upside_pct"],
        "expected_downside_pct": plan["expected_downside_pct"],
        "risk_reward": plan["risk_reward"],
        "explanation": entry["explanation"],
    }


def save_reports(
    ranked_entries: list[dict[str, Any]],
    config: dict[str, Any],
    report_date: str,
) -> tuple[Path, Path]:
    """Save the ranked report as both CSV and JSON, timestamped by report_date."""
    reports_dir = resolve_path(config["data"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)

    rows = [_entry_to_flat_row(entry) for entry in ranked_entries]

    csv_path = reports_dir / f"report_{report_date}.csv"
    json_path = reports_dir / f"report_{report_date}.json"

    pd.DataFrame(rows).to_csv(csv_path, index=False)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"report_date": report_date, "assets": rows}, f, indent=2)

    return csv_path, json_path


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
