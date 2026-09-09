"""AI Quant Data Collector and Signal Engine - version 1.

Collects daily data, computes indicators, scores and ranks assets, and
sends a Telegram report. Does NOT place trades, connect to a broker, or
touch real money.
"""

from __future__ import annotations

import logging
import sys

from . import data_fetcher, indicators, report_writer, scoring, signals, telegram_bot
from .utils import get_env_var, load_config, load_env, safe_run, setup_logging


def analyze_symbol(symbol: str, config: dict, logger: logging.Logger) -> dict | None:
    df = data_fetcher.fetch_symbol_history(symbol, config, logger)
    df_ind = indicators.compute_all_indicators(df, config)
    snapshot = indicators.latest_snapshot(df_ind)

    score_result = scoring.score_asset(snapshot, config)
    score = score_result["score"]
    signal = signals.classify_by_score(score, config)
    trade_plan = signals.build_trade_plan(snapshot, config)
    explanation = signals.build_explanation(signal, score_result["breakdown"], trade_plan, snapshot)

    return {
        "symbol": symbol,
        "score": score,
        "signal": signal,
        "snapshot": snapshot,
        "score_breakdown": score_result["breakdown"],
        "trend_status": signals.trend_status(snapshot),
        "momentum_status": signals.momentum_status(snapshot),
        "trade_plan": trade_plan,
        "explanation": explanation,
    }


def run(config_path: str | None = None) -> int:
    load_env()
    config = load_config(config_path)
    logger = setup_logging(config)

    logger.info("Starting AI Quant Bot run (data collection & analysis only, no trading)")

    symbols = config["tickers"]
    entries: list[dict] = []
    failed_symbols: list[str] = []

    for symbol in symbols:
        result = safe_run(logger, symbol, lambda s=symbol: analyze_symbol(s, config, logger))
        if result is not None:
            entries.append(result)
        else:
            failed_symbols.append(symbol)

    if not entries:
        logger.error("No symbols could be analyzed. Aborting run.")
        return 1

    entries.sort(key=lambda e: e["score"], reverse=True)
    for i, entry in enumerate(entries, start=1):
        entry["rank"] = i

    report_date = report_writer.today_str()
    csv_path, json_path = report_writer.save_reports(entries, config, report_date)
    logger.info("Saved report: %s | %s", csv_path, json_path)

    report_text = report_writer.format_report_text(entries, report_date, failed_symbols)

    if config.get("telegram", {}).get("enabled", True):
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

    logger.info("Run complete. %d/%d symbols analyzed.", len(entries), len(symbols))
    return 0


if __name__ == "__main__":
    sys.exit(run())
