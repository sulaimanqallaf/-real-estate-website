"""AI Quant Trading Research Bot - version 1.

Collects daily data, runs indicator + strategy + options-skew analysis, scores and
ranks tickers, and sends a Telegram report. Does NOT place trades, connect to a
broker, use margin, trade options, or short - research and alerting only.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import pandas as pd

from . import (
    big_money,
    data_collector,
    dataset_builder,
    indicators,
    market_regime,
    options_skew,
    paper_trade_tracker,
    paper_trades,
    portfolio_risk,
    quant_agent,
    report_writer,
    risk_manager,
    signal_scorer,
    strategy_memory,
)
from .data_providers import macro_provider, options_flow_provider
from .execution import execution_policy, order_manager
from .execution.broker import Broker
from .ml import model_registry as ml_model_registry
from .ml import predictor as ml_predictor
from .strategies import mean_reversion, momentum_breakout, skew_map, trend_following
from .utils import get_env_var, load_config, load_env, resolve_path, safe_run, setup_logging


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
    skip_tickers: set[str] | None = None,
) -> None:
    """Send one Approve/Reject/Watch Only message per Top Candidate.

    Deliberately reuses report_writer.select_top_candidates() rather than
    filtering ticker_results itself - that's the single choke point that already
    keeps Aggressive candidates out unless aggressive_mode.enabled is true AND
    they're actually in Top Candidates, so this function can't accidentally offer
    an approval button on something that shouldn't have one. High Risk Dip
    Watchlist entries never reach this function at all.

    `skip_tickers` (Phase 7): symbols the execution layer already
    AUTO_EXECUTEd this run - never send a redundant approval button for
    something that already bypassed approval and placed a real PAPER
    order (Part S).
    """
    from . import telegram_bot

    skip_tickers = skip_tickers or set()
    for entry in report_writer.select_top_candidates(ticker_results, config):
        if entry["symbol"] in skip_tickers:
            continue

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


def _fetch_macro_snapshot_safely(config: dict[str, Any], logger: logging.Logger) -> dict[str, Any] | None:
    """Best-effort, always-safe macro context for the report: fed funds rate
    and the 10Y-2Y spread, gated entirely on FRED_API_KEY. Returns None (never
    a fabricated value) if FRED isn't configured or a fetch fails - see
    data_providers/macro_provider.py."""
    if not config.get("providers", {}).get("fred", {}).get("enabled", True):
        return None

    def _fetch():
        fed_funds = macro_provider.fetch_series_latest(macro_provider.SERIES_FED_FUNDS_RATE)
        ten_year = macro_provider.fetch_series_latest(macro_provider.SERIES_10Y_TREASURY)
        two_year = macro_provider.fetch_series_latest(macro_provider.SERIES_2Y_TREASURY)
        if not fed_funds.ok and not ten_year.ok:
            return None
        spread = None
        if ten_year.ok and two_year.ok:
            spread = macro_provider.compute_yield_curve_spread(ten_year.data[0], two_year.data[0])
        return {
            "fed_funds_rate": fed_funds.data[0].value if fed_funds.ok else None,
            "yield_curve_spread": spread,
        }

    return safe_run(logger, "macro context (FRED)", _fetch)


def _compute_big_money_scores(
    ticker_results: list[dict[str, Any]], config: dict[str, Any], logger: logging.Logger
) -> dict[str, Any]:
    """Best-effort Big Money component scoring per ticker - context only, run
    AFTER the regime/portfolio pipeline so it has no mechanism to influence
    those decisions (see big_money.apply_big_money_ranking_filter's
    docstring). `institutional_facts`/`insider_features` are None here: this
    phase ships the SEC 13F/Form4 parsing and point-in-time logic fully
    tested (see src/data_providers/sec_provider.py), but the daily bot does
    not yet maintain a persisted filing-history store to diff against, so
    those two components honestly report Data Unavailable in live runs today
    rather than fabricating a score from a single snapshot with no prior
    quarter to compare - see README "Big Money Data Engine"."""
    flow_provider = options_flow_provider.get_default_provider(config)
    scores: dict[str, Any] = {}
    for entry in ticker_results:
        ticker = entry["symbol"]
        relative_volume = entry["snapshot"].get("relative_volume")

        flow_result = safe_run(logger, f"{ticker} options flow", lambda t=ticker: flow_provider.fetch_events(t, config))
        flow_events = flow_result.data if flow_result is not None and flow_result.ok else None

        scores[ticker] = big_money.compute_big_money_score(
            ticker,
            institutional_facts=None,
            insider_features=None,
            flow_events=flow_events,
            relative_volume=relative_volume,
        )
    return scores


def _compute_quant_assessments(
    ticker_results: list[dict[str, Any]],
    price_data: dict[str, Any],
    regime: market_regime.MarketRegime,
    config: dict[str, Any],
    logger: logging.Logger,
) -> dict[str, quant_agent.QuantAssessment]:
    """Phase 6 Quant/ML Intelligence layer - runs AFTER the regime/portfolio
    pipeline and Big Money scoring, strictly as decision-support context
    (see quant_agent.py's module docstring for the hard invariant: this can
    never promote an Avoid, never bypass a risk/regime/portfolio rejection).

    **This never trains anything** - `ml.trainer` is a separate, offline
    entry point (Part X); this only ever LOADS whatever CHAMPION models are
    already registered under `config.ml.registry_dir`, and degrades cleanly
    to "Data Unavailable" per-ticker if none exist yet (the common case in a
    fresh checkout, exactly like the Big Money providers in Phase 5 with no
    credentials configured)."""
    ml_cfg = config.get("ml", {})
    horizon = ml_cfg.get("primary_horizon", 10)
    registry_dir = ml_cfg.get("registry_dir", "data/models")
    registry = ml_model_registry.ModelRegistry(resolve_path(registry_dir))

    benchmark_price_data = {s: price_data[s] for s in ("SPY", "QQQ") if s in price_data}

    assessments: dict[str, quant_agent.QuantAssessment] = {}
    for entry in ticker_results:
        ticker = entry["symbol"]
        risk = entry.get("best_risk_result")
        strategy_name = risk["strategy"] if risk else None

        def _predict() -> ml_predictor.MLPrediction | None:
            df = price_data.get(ticker)
            if df is None or len(df) < 2:
                return None
            row = dataset_builder.build_feature_row(
                ticker, df, len(df) - 1, config, benchmark_price_data=benchmark_price_data
            )
            feature_row = pd.DataFrame([row])
            return ml_predictor.predict_for_ticker(ticker, feature_row, registry, horizon, config)

        prediction = safe_run(logger, f"{ticker} ML prediction", _predict)

        strategy_edge = None
        if strategy_name is not None:
            strategy_edge = safe_run(
                logger, f"{ticker} strategy memory",
                lambda s=strategy_name: strategy_memory.edge_for_strategy_in_regime(config, s, regime.primary),
            )

        assessments[ticker] = quant_agent.assess_candidate(entry, prediction, strategy_edge, config)

    return assessments


def _classify_execution_decisions(ticker_results: list[dict[str, Any]], config: dict[str, Any]) -> None:
    """Attaches entry["execution_decision"] to every ticker_result (Phase 7
    Part D) - purely informational unless execution.mode == "IBKR_PAPER"
    AND both autonomous_paper.enabled and auto_execute.enabled are true
    (both default false). See execution_policy.py's hard invariant: this
    can only classify a candidate as MORE cautious than what label/
    individual-risk/regime/portfolio-risk already decided, never less."""
    for entry in ticker_results:
        entry["execution_decision"] = execution_policy.classify_candidate(entry, config)


def _attempt_auto_execution(
    entry: dict[str, Any],
    broker: Broker,
    manager: order_manager.OrderManager,
    config: dict[str, Any],
    logger: logging.Logger,
    trade_id: str,
) -> dict[str, Any]:
    """One AUTO_EXECUTE candidate's execution-time re-checks + broker
    submission - the exact same re-check discipline as
    execution.approval_bridge.execute_approved_trade (Part R), applied to
    the autonomous path (Part S) instead of a Telegram button. The
    candidate's OWN entry price (this run's freshly fetched signal price)
    is also "current_market_price" here since auto-execution happens
    immediately after analysis, with no human-approval delay to go stale
    over - unlike the approval-bridge path, where real time elapses."""
    from .execution import approval_bridge

    final = report_writer.final_position(entry)
    record = {
        "symbol": entry["symbol"], "strategy": final["strategy"], "score": entry["score"],
        "entry": final["entry"], "stop_loss": final["stop_loss"], "target": final["target"],
        "shares": final["shares"], "dollar_risk": final["dollar_risk"],
        "regime_at_entry": (entry.get("regime_evaluation") or {}).get("regime"),
    }
    return approval_bridge.execute_approved_trade(record, config, broker, manager, current_market_price=final["entry"], logger=logger, trade_id=trade_id)


def _process_execution_layer(
    ticker_results: list[dict[str, Any]],
    report_date: str,
    config: dict[str, Any],
    logger: logging.Logger,
    token: str | None,
    chat_id: str | None,
    broker: Broker | None = None,
) -> set[str]:
    """Phase 7 execution layer entry point from the daily run. Returns the
    set of ticker symbols that were auto-executed this run, so
    `_send_paper_trade_approvals` can skip sending a redundant approval
    button for something that already bypassed approval (Part S).

    `broker` is normally None in production - a real `IBKRClient` is only
    constructed here if `execution.mode == "IBKR_PAPER"` AND at least one
    candidate actually classified as AUTO_EXECUTE (never connects to
    anything for a DRY_RUN run, or a run with nothing to auto-execute).
    Tests inject a `FakeBroker` directly instead.
    """
    _classify_execution_decisions(ticker_results, config)

    execution_mode = config.get("execution", {}).get("mode", "DRY_RUN")
    auto_candidates = [e for e in ticker_results if e["execution_decision"].decision == execution_policy.DECISION_AUTO_EXECUTE]

    if execution_mode != "IBKR_PAPER" or not auto_candidates:
        if execution_mode == "DRY_RUN" and auto_candidates:
            for entry in auto_candidates:
                logger.info("DRY_RUN: %s would AUTO_EXECUTE if execution.mode were IBKR_PAPER - no broker contacted.", entry["symbol"])
        return set()

    owns_broker = broker is None
    if owns_broker:
        from .execution.ibkr_client import IBKRClient

        broker = IBKRClient()
        try:
            broker.connect()
        except Exception as exc:  # noqa: BLE001
            logger.error("Auto-execution: could not connect to IBKR Paper (%s) - falling back to Telegram approval for all candidates this run.", exc)
            return set()

    executed_tickers: set[str] = set()
    try:
        journal = order_manager.ExecutionJournal(config.get("execution", {}).get("journal_path", "data/journal/executions.jsonl"))
        manager = order_manager.OrderManager(broker, config, journal)

        for entry in auto_candidates:
            trade_id = paper_trades.generate_trade_id(entry["symbol"], report_date)
            result = safe_run(logger, f"{entry['symbol']} auto-execution", lambda e=entry, tid=trade_id: _attempt_auto_execution(e, broker, manager, config, logger, tid))
            if result is None:
                continue
            if not result.get("executed"):
                logger.warning("Auto-execution skipped for %s: %s", entry["symbol"], result.get("reasons"))
                continue

            executed_tickers.add(entry["symbol"])
            final = report_writer.final_position(entry)
            pending_record = {
                "symbol": entry["symbol"], "strategy": final["strategy"], "signal": entry["label"], "score": entry["score"],
                "entry": final["entry"], "stop_loss": final["stop_loss"], "target": final["target"],
                "risk_reward": final["risk_reward"],
                "shares": result["managed"].intent.quantity, "dollar_risk": final["dollar_risk"],
                "regime_at_entry": (entry.get("regime_evaluation") or {}).get("regime"),
                "report_date": report_date, "decided_at": None,
            }
            paper_trades.record_paper_trade(pending_record, config, trade_id=trade_id)

            if token and chat_id:
                from . import telegram_bot
                from .execution import approval_bridge

                account_risk_pct = final["dollar_risk"] / config["risk"]["account_equity"] if config.get("risk", {}).get("account_equity") else None
                notice = approval_bridge.format_auto_execution_notice(pending_record, result["managed"], account_risk_pct)
                safe_run(logger, f"{entry['symbol']} auto-execution notice", lambda n=notice: telegram_bot.send_telegram_message(token, chat_id, n, logger))
    finally:
        if owns_broker:
            safe_run(logger, "broker disconnect", broker.disconnect)

    return executed_tickers


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

    # Big Money / institutional context - runs AFTER the regime/portfolio
    # pipeline above and only ever adds entry["big_money_score"] (plus, if
    # config.big_money.use_for_ranking is set, a purely additive cautionary
    # note); it has no mechanism to change best_risk_result, regime_evaluation,
    # or portfolio_evaluation, and apply_big_money_ranking_filter() explicitly
    # refuses to touch an Avoid-labeled entry. See big_money.py and README
    # "Big Money Data Engine".
    if config.get("big_money", {}).get("enabled", True):
        big_money_scores = safe_run(
            logger, "Big Money scoring", lambda: _compute_big_money_scores(ticker_results, config, logger)
        ) or {}
        big_money.apply_big_money_ranking_filter(ticker_results, big_money_scores, config)

    # Quant / ML Intelligence layer (Phase 6) - runs AFTER Portfolio Risk and
    # Big Money, per the authoritative pipeline order in README "Quant / ML
    # Intelligence Layer". Only ever loads already-registered CHAMPION
    # models (never trains - see src/ml/trainer.py, a separate offline
    # entry point) and only ever adds context/an optional additive
    # rejection - see quant_agent.py's hard invariant.
    if config.get("ml", {}).get("enabled", True):
        quant_assessments = safe_run(
            logger, "Quant/ML assessment", lambda: _compute_quant_assessments(ticker_results, price_data, regime, config, logger)
        ) or {}
        quant_agent.apply_quant_agent_filtering(ticker_results, quant_assessments, config)

    report_date = report_writer.today_str()

    # Autonomous IBKR PAPER execution layer (Phase 7) - runs AFTER every
    # deterministic gate and the Quant/ML layer above, per README's
    # authoritative pipeline. execution.mode defaults to DRY_RUN and
    # autonomous_paper.enabled/auto_execute.enabled both default to false,
    # so by default this only ATTACHES entry["execution_decision"] for
    # reporting and never contacts a broker - see execution_policy.py and
    # _process_execution_layer's docstring for the full fail-closed
    # contract. Best-effort token/chat_id here (never raises) purely so an
    # auto-execution notice can be sent; the trade itself never depends on
    # Telegram being configured.
    import os

    auto_executed_tickers = safe_run(
        logger, "execution layer",
        lambda: _process_execution_layer(
            ticker_results, report_date, config, logger,
            os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID"),
        ),
    ) or set()

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
                _send_paper_trade_approvals(ticker_results, report_date, token, chat_id, config, logger, skip_tickers=auto_executed_tickers)
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
