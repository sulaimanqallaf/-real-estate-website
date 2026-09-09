"""Format the daily Telegram report, persist CSV/JSON snapshots, and append the journal."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import performance_tracker, risk_manager
from .strategies import trend_following
from .utils import resolve_path


def final_position(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The position to actually show/act on for a Top Candidate: the portfolio-
    (and regime-) adjusted sizing when that pipeline has run, falling back to
    the raw individual-risk result when it hasn't (e.g. an older/synthetic entry
    with no portfolio_evaluation attached at all) - kept backward compatible
    rather than requiring every caller to have run the full pipeline."""
    portfolio_eval = entry.get("portfolio_evaluation")
    if portfolio_eval is not None and portfolio_eval.get("position") is not None:
        return portfolio_eval["position"]
    return entry.get("best_risk_result")


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
    individual = entry["best_risk_result"]
    final = final_position(entry)
    plan_str = ""
    if final and final["tradeable"]:
        plan_str = (
            f"Entry zone: {final['entry']}\n"
            f"Target: {final['target']}\n"
            f"Stop loss: {final['stop_loss']}\n"
            f"Expected upside: {_fmt(final['expected_upside_pct'], '%')}\n"
            f"Expected downside: {_fmt(final['expected_downside_pct'], '%')}\n"
            f"Risk/reward: {_fmt(final['risk_reward'])}\n"
            f"Suggested size: {final['shares']} shares (~${final['dollar_risk']:.0f} at risk)\n"
            f"Strategy: {final['strategy']}\n"
        )
        plan_str += format_sizing_context(entry, individual, final)

    return (
        f"{entry['symbol']}\n"
        f"Signal: {entry['label']}\n"
        f"Score: {entry['score']}/100\n"
        f"Price: {_fmt(entry['snapshot']['close'])}\n"
        f"{plan_str}"
        f"Options skew: {entry['skew_classification']}\n"
        f"Reason: {entry['explanation']}"
    )


def format_sizing_context(entry: dict[str, Any], individual: dict[str, Any], final: dict[str, Any]) -> str:
    """Item 19: show the sizing chain (individual -> regime -> portfolio) plus
    sector and portfolio-risk-after-trade context, whenever that pipeline
    actually ran. Silent (empty string) for an entry with no regime/portfolio
    evaluation attached at all, rather than printing misleading placeholder text."""
    regime_eval = entry.get("regime_evaluation")
    portfolio_eval = entry.get("portfolio_evaluation")
    if regime_eval is None or portfolio_eval is None:
        return ""

    lines = [f"Individual suggested size: {individual['shares']} shares"]

    regime_shares = math.floor(individual["shares"] * regime_eval["combined_multiplier"])
    if regime_eval["combined_multiplier"] < 1.0:
        lines.append(f"Regime-adjusted size: {regime_shares} shares ({regime_eval['regime']}, {regime_eval['status']})")

    if final["shares"] != regime_shares:
        lines.append(f"Final portfolio-adjusted size: {final['shares']} shares")
    elif regime_eval["combined_multiplier"] < 1.0:
        lines.append(f"Final size: {final['shares']} shares (unchanged by portfolio risk)")

    lines.append(f"Sector: {portfolio_eval['sector']}")

    if portfolio_eval.get("total_open_risk_after") is not None:
        lines.append(f"Portfolio open risk after this trade: {portfolio_eval['total_open_risk_after'] * 100:.2f}%")

    if portfolio_eval["warnings"]:
        lines.append("Portfolio note: " + " ".join(portfolio_eval["warnings"]))

    return "\n".join(lines) + "\n"


def format_high_risk_dip_block(entry: dict[str, Any]) -> str:
    """One "High Risk Dip Watchlist" entry - informational only, never a trade plan."""
    aggressive = entry["mean_reversion_aggressive_result"]
    risk = entry["aggressive_risk_result"]

    lines = [entry["symbol"], f"Price: {_fmt(entry['snapshot']['close'])}", aggressive["candidate"]["note"]]

    if risk:
        lines.append(f"Would-be entry: {risk['entry']}")
        lines.append(f"Would-be target: {risk['target']}")
        lines.append(f"Would-be stop loss: {risk['stop_loss']}")
        lines.append(f"Would-be risk/reward: {_fmt(risk['risk_reward'])}")
        if risk["tradeable"]:
            lines.append("Risk-rule check: would otherwise pass every risk rule")
        else:
            lines.append("Risk-rule check: blocked - " + "; ".join(risk["blocked_reasons"]))

    return "\n".join(lines)


def format_high_risk_dip_watchlist(ticker_results: list[dict[str, Any]], config: dict[str, Any]) -> str:
    aggressive_enabled = config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"]
    mode_label = (
        "ENABLED - can enter Top Candidates/journal once it clears every risk rule"
        if aggressive_enabled
        else "DISABLED - informational only, never auto-approved or paper traded"
    )

    triggered_entries = [
        r
        for r in ticker_results
        if r.get("mean_reversion_aggressive_result") and r["mean_reversion_aggressive_result"].get("candidate")
    ]

    if triggered_entries:
        body = "\n\n".join(format_high_risk_dip_block(e) for e in triggered_entries)
    else:
        body = "No aggressive dip setups today."

    return f"High Risk Dip Watchlist (Aggressive Mode: {mode_label}):\n\n{body}"


def format_market_regime_section(regime: Any, spy_trend: str, qqq_trend: str) -> str:
    """Item 18. `regime` is a market_regime.MarketRegime."""
    risk_state_label = "Risk-On" if regime.risk_state == "risk_on" else "Risk-Off"
    volatility_label = "Elevated" if regime.volatility == "elevated" else "Normal"
    return (
        f"Market Regime: {regime.primary}\n"
        f"Risk State: {risk_state_label}\n"
        f"Volatility: {volatility_label}\n"
        f"SPY trend: {spy_trend}\n"
        f"QQQ trend: {qqq_trend}\n"
        f"{regime.explanation}"
    )


def format_portfolio_risk_summary(config: dict[str, Any]) -> str:
    """Item 20. Self-contained (loads paper_trades.csv itself, same pattern as
    format_paper_trading_summary) so main.py doesn't have to thread portfolio
    state through separately. Prints one clean line instead of misleading
    zero-value stats when there are no open positions."""
    from . import paper_trades
    from . import portfolio_risk as portfolio_risk_module

    header = "Portfolio Risk:"
    state = portfolio_risk_module.compute_portfolio_state(paper_trades.load_paper_trades_df(config), config)

    if state["num_open_positions"] == 0:
        return f"{header}\n\nNo open paper positions - full risk budget available."

    cfg = config["portfolio_risk"]
    available_budget_fraction = max(0.0, cfg["max_total_open_risk_pct"] - state["total_open_risk_fraction"])

    lines = [
        f"Open positions: {state['num_open_positions']}/{cfg['max_open_positions']}",
        f"Open risk: {state['total_open_risk_fraction'] * 100:.2f}% (limit {cfg['max_total_open_risk_pct'] * 100:.1f}%)",
        f"Gross exposure: {state['gross_exposure_fraction'] * 100:.2f}%",
    ]
    if state["largest_sector"]:
        lines.append(f"Largest sector: {state['largest_sector']} ({state['largest_sector_fraction'] * 100:.2f}%)")
    lines.append(f"Available risk budget: {available_budget_fraction * 100:.2f}%")

    return f"{header}\n\n" + "\n".join(lines)


def format_paper_trading_summary(config: dict[str, Any]) -> str:
    """Daily "Paper Trading Performance" section - section 9 of the lifecycle
    phase. Shows only what the data actually supports: with zero closed trades
    it says so plainly instead of printing a wall of fake 0% metrics."""
    portfolio = performance_tracker.compute_portfolio_performance(config)
    header = "Paper Trading Performance:"

    if not portfolio["has_data"]:
        if portfolio["total_trades"] == 0:
            return f"{header}\n\nNo paper trades yet."
        return f"{header}\n\nNo closed paper trades yet. {portfolio['open_trades']} open position(s) being tracked."

    breakdown = performance_tracker.compute_strategy_breakdown(config)
    by_strategy = breakdown.get("by_strategy", {})

    best_label = worst_label = "N/A"
    if by_strategy:
        ranked = sorted(by_strategy.items(), key=lambda kv: kv[1]["total_pnl_dollars"], reverse=True)
        name, stats = ranked[0]
        best_label = f"{name} (${stats['total_pnl_dollars']:.2f}, {stats['sample_size']} trades)"
        if len(ranked) > 1:
            name, stats = ranked[-1]
            worst_label = f"{name} (${stats['total_pnl_dollars']:.2f}, {stats['sample_size']} trades)"
        else:
            worst_label = "N/A (only one strategy has closed trades so far)"

    total_pnl_line = f"Total P&L: ${portfolio['total_pnl_dollars']:.2f}"
    if portfolio["total_pnl_pct"] is not None:
        total_pnl_line += f" ({portfolio['total_pnl_pct']:.2f}%)"

    return (
        f"{header}\n\n"
        f"Open positions: {portfolio['open_trades']}\n"
        f"Closed trades: {portfolio['closed_trades']}\n"
        f"Win rate: {portfolio['win_rate_pct']:.1f}%\n"
        f"{total_pnl_line}\n"
        f"Best strategy: {best_label}\n"
        f"Worst strategy: {worst_label}"
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
    """Candidates actually worth surfacing. All of these are required:

    1. The risk manager approved the trade (`best_risk_result["tradeable"]`).
    2. If the candidate is Aggressive mean reversion, aggressive_mode.enabled must
       be true.
    3. The ticker's overall signal label is not Avoid.
    4. Risk/reward clears `risk.min_risk_reward_ratio` - folded into (1), since
       evaluate_candidate() already rejects anything below that threshold.

    1-4 are risk_manager.passes_universal_gates() - see that function; it's
    shared with portfolio_risk.run_regime_and_portfolio_pipeline() so both this
    function and that pipeline agree on exactly what "already qualifies" means.

    5. The Market Regime Filter and Portfolio Risk Manager (run earlier in
       main.py via portfolio_risk.run_regime_and_portfolio_pipeline, which
       attaches entry["portfolio_evaluation"]) did not REJECT it. An entry with
       no portfolio_evaluation attached at all (never run through that pipeline)
       is treated as passing this condition - backward compatible with anything
       that only ever ran the pre-Phase-4 pipeline, rather than silently
       requiring every caller to adopt the new one.

    Enabling aggressive_mode only satisfies (2) - it makes an Aggressive candidate
    ELIGIBLE to be considered here, it does not exempt it from (3), (4), or (5). A
    ticker crashing hard enough to trigger Aggressive mean reversion very often
    lands on an Avoid label on the universal 0-100 checklist regardless of how the
    dip-buy trade itself scores - enabling the flag does not change that.

    This is also the single choke point for both the Telegram Top Candidates
    section AND the trade journal (append_to_journal calls this too), so (2) is
    re-checked here independently of main.py's analyze_symbol (which already keeps
    Aggressive candidates out of best_risk_result while disabled) - a deliberate
    second, defense-in-depth check on a rule specified as a hard "must never".
    """
    top_n = config["telegram"]["top_candidates_limit"]

    def is_eligible(entry: dict[str, Any]) -> bool:
        if not risk_manager.passes_universal_gates(entry, config):
            return False
        portfolio_eval = entry.get("portfolio_evaluation")
        if portfolio_eval is not None and portfolio_eval["decision"] == "REJECT":
            return False
        return True

    tradeable_entries = [r for r in ticker_results if is_eligible(r)]
    tradeable_entries.sort(key=lambda r: r["score"], reverse=True)
    return tradeable_entries[:top_n]


def format_report_text(
    ticker_results: list[dict[str, Any]],
    report_date: str,
    failed_symbols: list[str],
    config: dict[str, Any],
    regime: Any = None,
) -> str:
    """`regime` is the market_regime.MarketRegime computed once in main.py from
    SPY/QQQ (see market_regime.classify_regime); pass None to fall back to the
    older, simpler Bullish/Defensive/Neutral line (e.g. if market_regime.enabled
    is false, or for a caller that hasn't adopted the Phase 4 pipeline)."""
    top_candidates = select_top_candidates(ticker_results, config)

    avoid_symbols = [r["symbol"] for r in ticker_results if r["label"] == "Avoid"]

    spy_result = next((r for r in ticker_results if r["symbol"] == config["benchmark_ticker"]), None)
    qqq_result = next((r for r in ticker_results if r["symbol"] == "QQQ"), None)
    spy_trend = trend_following.describe_trend(spy_result["snapshot"]) if spy_result else "N/A"
    qqq_trend = trend_following.describe_trend(qqq_result["snapshot"]) if qqq_result else "N/A"

    header = f"Daily AI Quant Report\n{report_date}\n(Data collection & analysis only — no trades executed)"

    if top_candidates:
        candidates_text = "\n\n".join(format_candidate_block(e) for e in top_candidates)
        if config.get("paper_trading", {}).get("enabled", True):
            candidates_text += (
                "\n\n(Each candidate above was also sent as its own message with Approve Paper "
                "Trade / Reject / Watch Only buttons - tap one to record your decision. No real "
                "trades are placed either way.)"
            )
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

    # Item 17: candidates that passed individual risk but were rejected by
    # market regime or portfolio risk still get an explicit, human-readable
    # reason here, distinct from an individual-risk near-miss above.
    regime_or_portfolio_rejections = [
        r
        for r in ticker_results
        if r.get("portfolio_evaluation") is not None
        and r["portfolio_evaluation"]["decision"] == "REJECT"
        and r["symbol"] not in top_candidate_symbols
    ]
    rejection_lines = [
        f"- {r['symbol']}: {'; '.join(r['portfolio_evaluation']['rejection_reasons'])}"
        for r in regime_or_portfolio_rejections
    ]

    risk_warnings = [
        "This report is research/education only. No trades were placed. No margin, options, or short positions "
        "are used in Version 1.",
    ]
    if near_miss_lines:
        risk_warnings.append("Setups that triggered but were blocked by risk rules:")
        risk_warnings.extend(near_miss_lines)
    if rejection_lines:
        risk_warnings.append("Setups blocked by market regime or portfolio risk:")
        risk_warnings.extend(rejection_lines)

    if regime is not None:
        regime_section = format_market_regime_section(regime, spy_trend, qqq_trend)
    else:
        regime_section = f"Market regime: {determine_market_regime(spy_trend, qqq_trend)}\nSPY trend: {spy_trend}\nQQQ trend: {qqq_trend}"

    summary = (
        f"{regime_section}\n\n"
        f"Best sector/ETF: {best_sector_etf(ticker_results, config)}\n"
        f"Avoid list: {', '.join(avoid_symbols) if avoid_symbols else 'None'}"
    )

    footer_parts = [f"Risk warnings:\n" + "\n".join(risk_warnings)]
    if failed_symbols:
        footer_parts.append("Skipped (data error): " + ", ".join(failed_symbols))
    footer = "\n\n".join(footer_parts)

    high_risk_section = format_high_risk_dip_watchlist(ticker_results, config)

    sections = [header, summary, f"Top Candidates:\n\n{candidates_text}", high_risk_section]
    if config.get("portfolio_risk", {}).get("enabled", True):
        sections.append(format_portfolio_risk_summary(config))
    if config.get("paper_trading", {}).get("enabled", True):
        sections.append(format_paper_trading_summary(config))
    sections.append(footer)

    return "\n\n".join(sections)


def _entry_to_flat_row(entry: dict[str, Any]) -> dict[str, Any]:
    snap = entry["snapshot"]
    risk = entry["best_risk_result"] or {}
    aggressive_result = entry.get("mean_reversion_aggressive_result")
    aggressive_triggered = bool(aggressive_result and aggressive_result.get("candidate"))
    aggressive_risk = entry.get("aggressive_risk_result") or {}
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
        "aggressive_dip_triggered": aggressive_triggered,
        "aggressive_entry": aggressive_risk.get("entry"),
        "aggressive_stop_loss": aggressive_risk.get("stop_loss"),
        "aggressive_target": aggressive_risk.get("target"),
        "aggressive_risk_reward": aggressive_risk.get("risk_reward"),
        "aggressive_would_pass_risk_rules": aggressive_risk.get("tradeable"),
        "aggressive_blocked_reasons": "; ".join(aggressive_risk.get("blocked_reasons", [])) if aggressive_risk else "",
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

    Reuses select_top_candidates(), which is where the Aggressive mean-reversion
    "must never be paper traded... unless explicitly enabled" rule is enforced -
    this function never needs its own copy of that check.
    """
    journal_dir = resolve_path(config["data"]["journal_dir"])
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_path = journal_dir / "trade_journal.csv"

    rows = []
    for entry in select_top_candidates(ticker_results, config):
        risk = final_position(entry)
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
