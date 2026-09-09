"""AI Quant Trading Research Bot - version 1.

Collects daily data, runs indicator + strategy + options-skew analysis, scores and
ranks tickers, and sends a Telegram report. Does NOT place trades, connect to a
broker, use margin, trade options, or short - research and alerting only.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from . import data_collector, indicators, options_skew, report_writer, risk_manager, signal_scorer
from .strategies import mean_reversion, momentum_breakout, skew_map, trend_following
from .utils import get_env_var, load_config, load_env, safe_run, setup_logging


def analyze_symbol(
    symbol: str,
    snapshot: dict[str, float],
    skew_snapshot: options_skew.OptionChainSnapshot | None,
    benchmark_snapshot: dict[str, float] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    universe = config["strategy_universe"]
    aggressive_enabled = config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"]

    trend_result = trend_following.evaluate(snapshot, config) if symbol in universe["trend_following"] else None
    breakout_result = (
        momentum_breakout.evaluate(snapshot, config) if symbol in universe["momentum_breakout"] else None
    )

    mean_rev_safe_result = None
    mean_rev_aggressive_result = None
    aggressive_risk_result = None
    if symbol in universe["mean_reversion"]:
        mean_rev_safe_result = mean_reversion.evaluate(snapshot, config, mode="safe")
        mean_rev_aggressive_result = mean_reversion.evaluate(snapshot, config, mode="aggressive")
        # Always risk-evaluate the aggressive candidate for the "High Risk Dip
        # Watchlist" report section, regardless of aggressive_enabled - this is
        # purely informational (would it have passed?) and is never, by itself,
        # what makes an aggressive candidate eligible for Top Candidates.
        if mean_rev_aggressive_result.get("candidate"):
            aggressive_risk_result = risk_manager.evaluate_candidate(
                mean_rev_aggressive_result["candidate"], snapshot, config
            )

    # Aggressive candidates only enter the pool competing for best_risk_result (and
    # therefore Top Candidates / the trade journal) when explicitly enabled.
    raw_candidates = [
        r["candidate"] for r in (trend_result, breakout_result, mean_rev_safe_result) if r and r.get("candidate")
    ]
    if aggressive_enabled and mean_rev_aggressive_result and mean_rev_aggressive_result.get("candidate"):
        raw_candidates.append(mean_rev_aggressive_result["candidate"])

    best_risk_result = risk_manager.evaluate_best_candidate(raw_candidates, snapshot, config) if raw_candidates else None

    skew_classification = skew_map.classify_skew(
        snapshot["return_1m"], skew_snapshot.skew if skew_snapshot else None, config
    )

    score_result = signal_scorer.score_ticker(
        snapshot, trend_result, breakout_result, skew_classification, best_risk_result, config
    )

    return_vs_spy = None
    if benchmark_snapshot is not None:
        ret = snapshot["return_1m"]
        bench_ret = benchmark_snapshot["return_1m"]
        if ret == ret and bench_ret == bench_ret:  # NaN-safe
            return_vs_spy = ret - bench_ret

    entry = {
        "symbol": symbol,
        "snapshot": snapshot,
        "score": score_result["score"],
        "label": score_result["label"],
        "score_breakdown": score_result["breakdown"],
        "trend_result": trend_result,
        "breakout_result": breakout_result,
        "mean_reversion_safe_result": mean_rev_safe_result,
        "mean_reversion_aggressive_result": mean_rev_aggressive_result,
        "aggressive_risk_result": aggressive_risk_result,
        "skew_snapshot": skew_snapshot,
        "skew_classification": skew_classification,
        "best_risk_result": best_risk_result,
        "return_vs_spy": return_vs_spy,
    }
    entry["explanation"] = report_writer.build_explanation(entry)
    return entry


def run(config_path: str | None = None) -> int:
    load_env()
    config = load_config(config_path)
    logger = setup_logging(config)

    logger.info("Starting AI Quant Research Bot run (research/alerts only, no trading)")

    symbols = config["tickers"]
    price_data = data_collector.fetch_all_price_history(symbols, config, logger)
    failed_symbols = [s for s in symbols if s not in price_data]

    if not price_data:
        logger.error("No symbols could be fetched. Aborting run.")
        return 1

    snapshots: dict[str, dict[str, float]] = {}
    for symbol, df in price_data.items():
        df_ind = indicators.compute_all_indicators(df, config)
        snapshots[symbol] = indicators.latest_snapshot(df_ind)

    spot_prices = {s: snap["close"] for s, snap in snapshots.items()}
    skew_snapshots = options_skew.fetch_all_skew_snapshots(list(snapshots.keys()), spot_prices, config, logger)

    benchmark_snapshot = snapshots.get(config["benchmark_ticker"])

    ticker_results: list[dict[str, Any]] = []
    for symbol, snapshot in snapshots.items():
        result = safe_run(
            logger,
            symbol,
            lambda s=symbol, snap=snapshot: analyze_symbol(
                s, snap, skew_snapshots.get(s), benchmark_snapshot, config
            ),
        )
        if result is not None:
            ticker_results.append(result)
        else:
            failed_symbols.append(symbol)

    if not ticker_results:
        logger.error("No symbols could be analyzed. Aborting run.")
        return 1

    report_date = report_writer.today_str()
    csv_path, json_path = report_writer.save_reports(ticker_results, config, report_date)
    logger.info("Saved report: %s | %s", csv_path, json_path)

    journal_path = report_writer.append_to_journal(ticker_results, config, report_date)
    logger.info("Journal updated: %s", journal_path)

    report_text = report_writer.format_report_text(ticker_results, report_date, failed_symbols, config)

    if config.get("telegram", {}).get("enabled", True):
        from . import telegram_bot

        try:
            token = get_env_var("TELEGRAM_BOT_TOKEN")
            chat_id = get_env_var("TELEGRAM_CHAT_ID")
            max_chars = config["telegram"].get("max_message_chars", 3500)
            ok = telegram_bot.send_report(token, chat_id, report_text, max_chars, logger)
            if ok:
                logger.info("Telegram report sent successfully.")
            else:
                logger.error("Telegram report failed to send (see logged errors above).")
        except RuntimeError as exc:
            logger.error("Telegram not configured: %s", exc)
    else:
        logger.info("Telegram sending disabled in config; skipping send.")

    if failed_symbols:
        logger.warning("Symbols skipped due to data errors: %s", ", ".join(failed_symbols))

    logger.info("Run complete. %d/%d symbols analyzed.", len(ticker_results), len(symbols))
    return 0


if __name__ == "__main__":
    sys.exit(run())
