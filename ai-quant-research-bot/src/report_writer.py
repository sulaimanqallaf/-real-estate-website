"""Format the daily Telegram report, persist CSV/JSON snapshots, and append the journal."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .strategies import trend_following
from .utils import resolve_path


def _fmt(value: Any, suffix: str = "", digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "N/A"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return str(value)


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def determine_market_regime(spy_trend: str, qqq_trend: str) -> str:
    if spy_trend == trend_following.UPTREND and qqq_trend == trend_following.UPTREND:
        return "Bullish"
    if spy_trend == trend_following.DOWNTREND and qqq_trend == trend_following.DOWNTREND:
        return "Defensive"
    return "Neutral"


def best_sector_etf(ticker_results: list[dict[str, Any]], config: dict[str, Any]) -> str:
    etf_set = set(config.get("etf_tickers", []))
    etf_results = [r for r in ticker_results if r["symbol"] in etf_set]
    if not etf_results:
        return "N/A"
    best = max(etf_results, key=lambda r: r["score"])
    return f"{best['symbol']} (score {best['score']}/100)"


def format_candidate_block(entry: dict[str, Any]) -> str:
    risk = entry["best_risk_result"]
    plan_str = ""
    if risk and risk["tradeable"]:
        plan_str = (
            f"Entry zone: {risk['entry']}\n"
            f"Target: {risk['target']}\n"
            f"Stop loss: {risk['stop_loss']}\n"
            f"Expected upside: {_fmt(risk['expected_upside_pct'], '%')}\n"
            f"Expected downside: {_fmt(risk['expected_downside_pct'], '%')}\n"
            f"Risk/reward: {_fmt(risk['risk_reward'])}\n"
            f"Suggested size: {risk['shares']} shares (~${risk['dollar_risk']:.0f} at risk)\n"
            f"Strategy: {risk['strategy']}\n"
        )
    return (
        f"{entry['symbol']}\n"
        f"Signal: {entry['label']}\n"
        f"Score: {entry['score']}/100\n"
        f"Price: {_fmt(entry['snapshot']['close'])}\n"
        f"{plan_str}"
        f"Options skew: {entry['skew_classification']}\n"
        f"Reason: {entry['explanation']}"
    )


def build_explanation(entry: dict[str, Any]) -> str:
    breakdown = entry["score_breakdown"]
    positives = []
    if breakdown.get("above_sma_200") and breakdown.get("above_sma_50"):
        positives.append("price above both key moving averages")
    if breakdown.get("momentum_20d_positive") and breakdown.get("momentum_60d_positive"):
        positives.append("positive momentum on both windows")
    if breakdown.get("rsi_in_healthy_range"):
        positives.append("RSI in a healthy range")
    if breakdown.get("volume_above_avg"):
        positives.append("volume above average")
    if breakdown.get("trend_following_positive"):
        positives.append("trend-following filter is positive")
    if breakdown.get("momentum_breakout_active"):
        positives.append("momentum breakout active")
    if breakdown.get("favorable_options_skew"):
        positives.append("options positioning favorable")

    risk = entry["best_risk_result"]
    if risk and not risk["tradeable"]:
        return "Setup triggered but blocked: " + " ".join(risk["blocked_reasons"])

    if not positives:
        return "Insufficient favorable conditions across the scoring checklist."

    return (", ".join(positives) + ".").capitalize()


def select_top_candidates(ticker_results: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Candidates actually worth surfacing: risk-manager-approved AND not labeled Avoid.

    A strategy can compute a valid, tradeable entry/stop/target (e.g. Mean Reversion
    on a temporary dip) on a ticker whose overall 0-100 score is still weak - that's
    real and informative, but presenting it as a top pick right under "Signal: Avoid"
    would be self-contradictory. Both filters are required together.
    """
    top_n = config["telegram"]["top_candidates_limit"]
    tradeable_entries = [
        r
        for r in ticker_results
        if r["best_risk_result"] and r["best_risk_result"]["tradeable"] and r["label"] != "Avoid"
    ]
    tradeable_entries.sort(key=lambda r: r["score"], reverse=True)
    return tradeable_entries[:top_n]


def format_report_text(
    ticker_results: list[dict[str, Any]],
    report_date: str,
    failed_symbols: list[str],
    config: dict[str, Any],
) -> str:
    top_candidates = select_top_candidates(ticker_results, config)

    avoid_symbols = [r["symbol"] for r in ticker_results if r["label"] == "Avoid"]

    spy_result = next((r for r in ticker_results if r["symbol"] == config["benchmark_ticker"]), None)
    qqq_result = next((r for r in ticker_results if r["symbol"] == "QQQ"), None)
    spy_trend = trend_following.describe_trend(spy_result["snapshot"]) if spy_result else "N/A"
    qqq_trend = trend_following.describe_trend(qqq_result["snapshot"]) if qqq_result else "N/A"
    market_regime = determine_market_regime(spy_trend, qqq_trend) if spy_result and qqq_result else "N/A"

    header = f"Daily AI Quant Report\n{report_date}\n(Data collection & analysis only — no trades executed)"

    if top_candidates:
        candidates_text = "\n\n".join(format_candidate_block(e) for e in top_candidates)
    else:
        candidates_text = "No candidates cleared the risk rules today."

    top_candidate_symbols = {e["symbol"] for e in top_candidates}
    near_misses = [
        r
        for r in ticker_results
        if r["best_risk_result"]
        and not r["best_risk_result"]["tradeable"]
        and r["symbol"] not in top_candidate_symbols
    ]
    near_miss_lines = [
        f"- {r['symbol']}: {'; '.join(r['best_risk_result']['blocked_reasons'])}" for r in near_misses
    ]

    risk_warnings = [
        "This report is research/education only. No trades were placed. No margin, options, or short positions "
        "are used in Version 1.",
    ]
    if near_miss_lines:
        risk_warnings.append("Setups that triggered but were blocked by risk rules:")
        risk_warnings.extend(near_miss_lines)

    summary = (
        f"Market regime: {market_regime}\n"
        f"SPY trend: {spy_trend}\n"
        f"QQQ trend: {qqq_trend}\n"
        f"Best sector/ETF: {best_sector_etf(ticker_results, config)}\n"
        f"Avoid list: {', '.join(avoid_symbols) if avoid_symbols else 'None'}"
    )

    footer_parts = [f"Risk warnings:\n" + "\n".join(risk_warnings)]
    if failed_symbols:
        footer_parts.append("Skipped (data error): " + ", ".join(failed_symbols))
    footer = "\n\n".join(footer_parts)

    return f"{header}\n\n{summary}\n\nTop Candidates:\n\n{candidates_text}\n\n{footer}"


def _entry_to_flat_row(entry: dict[str, Any]) -> dict[str, Any]:
    snap = entry["snapshot"]
    risk = entry["best_risk_result"] or {}
    return {
        "symbol": entry["symbol"],
        "signal": entry["label"],
        "score": entry["score"],
        "price": snap["close"],
        "sma_50": snap["sma_50"],
        "sma_200": snap["sma_200"],
        "ema_50": snap["ema_50"],
        "ema_200": snap["ema_200"],
        "rsi_14": snap["rsi_14"],
        "atr_14": snap["atr_14"],
        "momentum_20d_pct": snap["momentum_20d"],
        "momentum_60d_pct": snap["momentum_60d"],
        "return_1m_pct": snap["return_1m"],
        "return_vs_spy_pct": entry.get("return_vs_spy"),
        "relative_volume": snap["relative_volume"],
        "daily_volatility_pct": snap["daily_volatility_pct"],
        "skew_classification": entry["skew_classification"],
        "strategy": risk.get("strategy"),
        "tradeable": risk.get("tradeable"),
        "entry": risk.get("entry"),
        "target": risk.get("target"),
        "stop_loss": risk.get("stop_loss"),
        "expected_upside_pct": risk.get("expected_upside_pct"),
        "expected_downside_pct": risk.get("expected_downside_pct"),
        "risk_reward": risk.get("risk_reward"),
        "shares": risk.get("shares"),
        "dollar_risk": risk.get("dollar_risk"),
        "blocked_reasons": "; ".join(risk.get("blocked_reasons", [])) if risk else "",
        "explanation": entry["explanation"],
    }


def save_reports(ticker_results: list[dict[str, Any]], config: dict[str, Any], report_date: str) -> tuple[Path, Path]:
    reports_dir = resolve_path(config["data"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)

    rows = [_entry_to_flat_row(entry) for entry in ticker_results]
    rows.sort(key=lambda r: r["score"], reverse=True)

    csv_path = reports_dir / f"report_{report_date}.csv"
    json_path = reports_dir / f"report_{report_date}.json"

    pd.DataFrame(rows).to_csv(csv_path, index=False)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"report_date": report_date, "tickers": rows}, f, indent=2)

    return csv_path, json_path


def append_to_journal(ticker_results: list[dict[str, Any]], config: dict[str, Any], report_date: str) -> Path:
    """Append every tradeable candidate alerted today to the trade journal CSV.

    Version 1 never executes trades, so this journal is a record of what was
    *alerted*, not of any position actually taken. Fill in exit_price/pnl/status
    manually as you track outcomes, or a later version can automate that.
    """
    journal_dir = resolve_path(config["data"]["journal_dir"])
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_path = journal_dir / "trade_journal.csv"

    rows = []
    for entry in select_top_candidates(ticker_results, config):
        risk = entry["best_risk_result"]
        rows.append(
            {
                "alert_date": report_date,
                "symbol": entry["symbol"],
                "strategy": risk["strategy"],
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
                "status": "ALERTED",
                "exit_price": "",
                "pnl": "",
                "notes": "",
            }
        )

    if not rows:
        return journal_path

    new_df = pd.DataFrame(rows)
    if journal_path.exists():
        existing = pd.read_csv(journal_path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(journal_path, index=False)
    return journal_path
