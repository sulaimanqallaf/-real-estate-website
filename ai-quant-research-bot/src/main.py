"""AI Quant Trading Research Bot - version 1.

Collects daily data, runs indicator + strategy + options-skew analysis, scores and
ranks tickers, and sends a Telegram report. Does NOT place trades, connect to a
broker, use margin, trade options, or short - research and alerting only.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from . import (
    data_collector,
    indicators,
    market_regime,
    options_skew,
    paper_trade_tracker,
    paper_trades,
    portfolio_risk,
    report_writer,
    risk_manager,
    signal_scorer,
)
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


def _send_paper_trade_approvals(
    ticker_results: list[dict[str, Any]],
    report_date: str,
    token: str,
    chat_id: str,
    config: dict[str, Any],
    logger: logging.Logger,
) -> None:
    """Send one Approve/Reject/Watch Only message per Top Candidate.

    Deliberately reuses report_writer.select_top_candidates() rather than
    filtering ticker_results itself - that's the single choke point that already
    keeps Aggressive candidates out unless aggressive_mode.enabled is true AND
    they're actually in Top Candidates, so this function can't accidentally offer
    an approval button on something that shouldn't have one. High Risk Dip
    Watchlist entries never reach this function at all.
    """
    from . import telegram_bot

    for entry in report_writer.select_top_candidates(ticker_results, config):

        def _send(e=entry):
            text = paper_trades.format_approval_message(e)
            keyboard = paper_trades.build_approval_keyboard(e["symbol"], report_date)
            message_id = telegram_bot.send_message_with_keyboard(token, chat_id, text, keyboard, logger)
            if message_id is None:
                raise RuntimeError(f"Failed to send approval message for {e['symbol']}")
            record = paper_trades.pending_record_from_entry(e, report_date, message_id, chat_id)
            paper_trades.save_pending_approval(record, config)

        safe_run(logger, f"{entry['symbol']} paper-trade approval message", _send)


def _classify_regime_safely(
    price_data: dict[str, Any],
    snapshots: dict[str, dict[str, float]],
    config: dict[str, Any],
    logger: logging.Logger,
) -> market_regime.MarketRegime:
    """Never let a regime-classification failure (missing SPY/QQQ data, e.g.)
    abort the run - fall back to the same safe SIDEWAYS/low-confidence default
    classify_regime() itself uses for missing indicator data."""
    fallback = market_regime.MarketRegime(
        primary=market_regime.SIDEWAYS, trend="mixed", volatility="normal", risk_state="risk_on",
        confidence=0.0, explanation="SPY/QQQ data unavailable - defaulting to SIDEWAYS.",
    )
    if "SPY" not in price_data or "QQQ" not in price_data or "SPY" not in snapshots or "QQQ" not in snapshots:
        logger.warning("SPY/QQQ data unavailable for regime classification - defaulting to SIDEWAYS.")
        return fallback

    result = safe_run(
        logger,
        "market regime classification",
        lambda: market_regime.classify_regime(price_data["SPY"], price_data["QQQ"], snapshots["SPY"], snapshots["QQQ"], config),
    )
    return result if result is not None else fallback


def _send_paper_trade_exit_notifications(
    closed_trades: list[dict[str, Any]],
    token: str,
    chat_id: str,
    logger: logging.Logger,
) -> None:
    """One Telegram message per trade closed THIS run - never a trade closed in
    a previous run, since paper_trade_tracker.check_open_trades() only ever
    returns newly-closed trades. That's what keeps this duplicate-free."""
    from . import telegram_bot

    for trade in closed_trades:

        def _send(t=trade):
            text = paper_trade_tracker.format_exit_notification(t)
            ok = telegram_bot.send_telegram_message(token, chat_id, text, logger)
            if not ok:
                raise RuntimeError(f"Failed to send exit notification for {t['trade_id']}")

        safe_run(logger, f"{trade['ticker']} paper-trade exit notification", _send)


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

    # Check existing OPEN paper positions against freshly fetched daily bars
    # BEFORE generating today's new candidates - see paper_trade_tracker.py for
    # the no-lookahead walk and the same-bar stop-wins conservative rule.
    newly_closed_trades: list[dict[str, Any]] = []
    if config.get("paper_trading", {}).get("enabled", True):
        newly_closed_trades = safe_run(
            logger,
            "paper trade lifecycle check",
            lambda: paper_trade_tracker.check_open_trades(price_data, config, logger),
        ) or []

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

    # Market Regime Filter, then Portfolio Risk Manager - both run on the full
    # ticker_results batch (score-descending, see portfolio_risk.py's module
    # docstring) BEFORE anything is saved/reported, so every downstream
    # consumer (reports, journal, Telegram approvals) already sees the final,
    # regime-and-portfolio-adjusted sizing via report_writer.select_top_candidates.
    regime = _classify_regime_safely(price_data, snapshots, config, logger)
    logger.info("Market regime: %s (risk_state=%s, confidence=%.2f)", regime.primary, regime.risk_state, regime.confidence)

    open_trades_df = paper_trades.load_paper_trades_df(config)
    safe_run(
        logger,
        "market regime + portfolio risk pipeline",
        lambda: portfolio_risk.run_regime_and_portfolio_pipeline(
            ticker_results, regime, open_trades_df, price_data, config, logger
        ),
    )

    report_date = report_writer.today_str()
    csv_path, json_path = report_writer.save_reports(ticker_results, config, report_date)
    logger.info("Saved report: %s | %s", csv_path, json_path)

    journal_path = report_writer.append_to_journal(ticker_results, config, report_date)
    logger.info("Journal updated: %s", journal_path)

    report_text = report_writer.format_report_text(ticker_results, report_date, failed_symbols, config, regime)

    if config.get("telegram", {}).get("enabled", True):
        from . import telegram_bot

        try:
            token = get_env_var("TELEGRAM_BOT_TOKEN")
            chat_id = get_env_var("TELEGRAM_CHAT_ID")

            if newly_closed_trades:
                _send_paper_trade_exit_notifications(newly_closed_trades, token, chat_id, logger)

            max_chars = config["telegram"].get("max_message_chars", 3500)
            ok = telegram_bot.send_report(token, chat_id, report_text, max_chars, logger)
            if ok:
                logger.info("Telegram report sent successfully.")
            else:
                logger.error("Telegram report failed to send (see logged errors above).")

            if config.get("paper_trading", {}).get("enabled", True):
                _send_paper_trade_approvals(ticker_results, report_date, token, chat_id, config, logger)
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
