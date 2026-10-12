"""Strategy Lab - the autonomous learning laboratory (Sprint 4,
Priority 4: "Connect research findings to the existing hypothesis
ledger, research sandbox, VectorBT and QuantStats. Automatically
generate, backtest and compare new strategy candidates. Apply
out-of-sample testing and prevent data leakage and overfitting. New
strategies may be proposed automatically but never deployed to active
trading without approval.").

This module invents no new backtest logic of its own - it is pure
orchestration over code this project already trusts:

- **Generate**: `generate_candidates()` sweeps a small grid of
  config-override values (e.g. `ma_window` 10/15/20/25/30 for Mean
  Reversion) into `sandbox.ResearchHypothesis` objects - a candidate
  this project generated itself, distinct from Task P2's
  paper-driven hypotheses (which still flow through the exact same
  `hypothesis_ledger`).
- **Backtest**: `backtester._run_strategy_backtest()` - the same
  production engine every other analytics module in this project
  uses, via a config COPY `sandbox._apply_overrides()` already merges
  the candidate's overrides into (the exact override-merge logic
  `sandbox.run_hypothesis()` itself uses, restricted to the same
  `ALLOWED_OVERRIDE_SECTIONS` allowlist - no new override path).
- **Out-of-sample test, leakage/overfitting defenses**:
  `analytics.walk_forward.run_walk_forward()` (Sprint 3 Task V2 -
  sequential, non-overlapping real-history folds, never the training
  window) and `analytics.monte_carlo.run_monte_carlo_stress_test()`
  (Phase 4 - bootstrap resampling of the candidate's OWN closed
  trades, answering "how much does this result depend on trade
  ORDER/luck," never fabricated below `MIN_TRADES_FOR_STRESS_TEST`).
  The SAME evidence-gated verdict Task V3 already built
  (`analytics.strategy_validation_report._evidence_verdict()`) decides
  whether a candidate counts as consistent, inconsistent, or lacking
  evidence - reused directly, not re-implemented.
- **VectorBT**: `analytics.backtester_crosscheck.run_crosscheck()` -
  independently-written-engine agreement check on the candidate's own
  fills, exactly Sprint 3's isolated, optional
  (`.venvs/oss_quant/`-gated) integration.
- **QuantStats**: the same `oss_quant_adapter.run_task
  ("performance_report", ...)` subprocess task `analytics.
  performance_report.py` already uses for live paper trades, fed the
  candidate's OWN per-trade backtest returns instead - real QuantStats
  metrics (Sharpe/Sortino/max drawdown/profit factor/win rate/exposure)
  on a BACKTEST candidate, honestly labeled as such.

**Never deployed without approval - structurally, not by promise.**
This module has no import of `src.execution` anywhere, and there is no
`promote()` function anywhere in this codebase (never has been, see
`research/hypothesis_ledger.py`'s own docstring) - a candidate's
scorecard is recorded into the hypothesis ledger as a plain row,
nothing more. Reviewing the scorecards and deciding whether anything
is worth a human-reviewed strategy change remains entirely outside
this module, exactly like every other research artifact this codebase
produces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..research.sandbox import ResearchHypothesis
from . import budget, checkpoint, heartbeat, registry

AGENT_NAME = "strategy_lab"

DEFAULT_WINDOW_GRID = [10, 15, 20, 25, 30]
DEFAULT_STRATEGY_SYMBOL_PAIRS: list[tuple[str, str]] = [
    ("Mean Reversion", "SPY"),
    ("Momentum Breakout", "SPY"),
    ("Trend Following", "SPY"),
]


def _lab_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("research", {}).get("strategy_lab", {})


@dataclass
class CandidateScorecard:
    hypothesis: ResearchHypothesis
    single_window_stats: dict[str, Any] | None
    walk_forward_report: Any  # analytics.walk_forward.WalkForwardReport
    verdict: str
    verdict_reason: str
    monte_carlo: dict[str, Any] | None
    vectorbt_crosscheck: dict[str, Any] | None
    quantstats_metrics: dict[str, Any] | None


def generate_candidates(strategy_name: str, symbol: str, window_values: list[int]) -> list[ResearchHypothesis]:
    """Sweeps `window_values` into one candidate per value for a
    strategy with a known tunable window (see `hypothesis_mapper.
    _WINDOW_OVERRIDE_KEY`); for a strategy with no such knob (Trend
    Following), returns exactly one default-parameter candidate -
    sweeping a non-existent parameter would just generate identical
    fake "candidates," never done here."""
    from ..research.hypothesis_mapper import _WINDOW_OVERRIDE_KEY, _strategy_config_section

    section = _strategy_config_section(strategy_name)
    override_key = _WINDOW_OVERRIDE_KEY.get(strategy_name)

    if override_key is None:
        return [
            ResearchHypothesis(
                hypothesis_id=f"lab_{section}_{symbol}_default",
                description=f"Strategy Lab candidate: {strategy_name} on {symbol} (default parameters, no tunable window for this strategy).",
                strategy_name=strategy_name, symbol=symbol, config_overrides={}, data_provenance="real_market_data",
            )
        ]

    return [
        ResearchHypothesis(
            hypothesis_id=f"lab_{section}_{symbol}_{window}",
            description=f"Strategy Lab candidate: {strategy_name} on {symbol} with a {window}-day window.",
            strategy_name=strategy_name, symbol=symbol,
            config_overrides={"strategies": {section: {override_key: window}}},
            data_provenance="real_market_data",
        )
        for window in window_values
    ]


def _per_trade_returns_by_exit_date(trades: list[Any]) -> dict[str, float]:
    """Same per-trade/per-exit-date compounding `analytics.
    performance_report.build_performance_report()` already uses for
    live trades - applied here to a candidate's own backtest `Trade`
    objects instead."""
    returns_by_exit: dict[str, float] = {}
    for t in trades:
        exit_date = str(t.exit_date)[:10]
        pnl_fraction = t.return_pct / 100.0
        existing = returns_by_exit.get(exit_date)
        returns_by_exit[exit_date] = ((1 + existing) * (1 + pnl_fraction) - 1) if existing is not None else pnl_fraction
    return returns_by_exit


def evaluate_candidate(
    hypothesis: ResearchHypothesis, price_history: dict[str, Any], config: dict[str, Any], logger: logging.Logger, data_provenance: str = "real_market_data"
) -> CandidateScorecard:
    """Runs the full multi-stage pipeline for one candidate: single-
    window backtest, walk-forward out-of-sample check, a Monte Carlo
    sequencing-risk stress test, and (best-effort, only when the
    isolated OSS-quant venv is set up) a VectorBT agreement check and
    real QuantStats metrics. Never raises on missing price data -
    returns an honest zero-evidence scorecard instead."""
    from .. import backtester
    from ..analytics import backtester_crosscheck, oss_quant_adapter, strategy_validation_report as svr, walk_forward
    from ..analytics.monte_carlo import run_monte_carlo_stress_test
    from ..research.sandbox import _apply_overrides

    overridden_config = _apply_overrides(config, hypothesis.config_overrides)
    df = price_history.get(hypothesis.symbol)

    if df is None or df.empty:
        empty_wf = walk_forward.WalkForwardReport(strategy_name=hypothesis.strategy_name, symbols=[hypothesis.symbol], fold_period="90d", folds=[])
        verdict, reason = svr._evidence_verdict({"total_trades": 0, "total_return_pct": None}, empty_wf)
        return CandidateScorecard(hypothesis, None, empty_wf, verdict, reason, None, None, None)

    lookback_days = backtester.period_to_days(overridden_config["backtest"]["lookback_period"])
    backtest_start = df.index.max() - timedelta(days=lookback_days)
    trades, equity_curve = backtester._run_strategy_backtest(hypothesis.strategy_name, [hypothesis.symbol], price_history, backtest_start, overridden_config, logger)
    single_window_stats = backtester._compute_stats(trades, equity_curve, overridden_config["backtest"]["initial_capital"])

    wf_report = walk_forward.run_walk_forward(hypothesis.strategy_name, [hypothesis.symbol], price_history, overridden_config, logger)
    verdict, reason = svr._evidence_verdict(single_window_stats, wf_report)

    monte_carlo_result = run_monte_carlo_stress_test([t.return_pct for t in trades]) if trades else None

    vectorbt_crosscheck = None
    quantstats_metrics = None
    if oss_quant_adapter.is_available(config):
        try:
            vectorbt_crosscheck = backtester_crosscheck.run_crosscheck(hypothesis.symbol, hypothesis.strategy_name, price_history, overridden_config, logger, data_provenance)
        except Exception as exc:  # noqa: BLE001 - a crosscheck failing must never block the rest of the scorecard
            logger.warning("Strategy Lab: VectorBT crosscheck for %s raised: %s", hypothesis.hypothesis_id, exc)
        if trades:
            try:
                qs_result = oss_quant_adapter.run_task("performance_report", {"returns": _per_trade_returns_by_exit_date(trades)}, config, logger)
                if qs_result and qs_result.get("ok"):
                    quantstats_metrics = qs_result["metrics"]
            except Exception as exc:  # noqa: BLE001 - same isolation as above
                logger.warning("Strategy Lab: QuantStats report for %s raised: %s", hypothesis.hypothesis_id, exc)

    return CandidateScorecard(hypothesis, single_window_stats, wf_report, verdict, reason, monte_carlo_result, vectorbt_crosscheck, quantstats_metrics)


def rank_candidates(scorecards: list[CandidateScorecard]) -> list[CandidateScorecard]:
    """Consistent-in-backtest candidates first, then inconsistent, then
    insufficient-evidence last; within a tier, higher mean walk-forward
    fold return first. Never claims a ranking means "better" in any
    forward-looking sense - see module docstring."""
    from ..analytics import strategy_validation_report as svr

    verdict_rank = {svr.VERDICT_CONSISTENT_IN_BACKTEST: 0, svr.VERDICT_INCONSISTENT: 1, svr.VERDICT_INSUFFICIENT_EVIDENCE: 2}

    def _sort_key(sc: CandidateScorecard) -> tuple[int, float]:
        mean_return = sc.walk_forward_report.mean_fold_return_pct
        return (verdict_rank.get(sc.verdict, 3), -(mean_return if mean_return is not None else float("-inf")))

    return sorted(scorecards, key=_sort_key)


def run_lab_cycle(config: dict[str, Any], logger: logging.Logger, now: datetime | None = None) -> dict[str, Any]:
    """One full lab cycle. Never raises - caught and recorded as a
    FAILED run in the registry, re-reported in the returned summary."""
    heartbeat.record_heartbeat(config, AGENT_NAME, now=now)
    registry.start_run(config, AGENT_NAME, now=now)

    lab_cfg = _lab_config(config)
    pairs: list[tuple[str, str]] = [tuple(p) for p in (lab_cfg.get("strategy_symbol_pairs") or DEFAULT_STRATEGY_SYMBOL_PAIRS)]
    window_grid: list[int] = lab_cfg.get("window_grid") or DEFAULT_WINDOW_GRID

    summary: dict[str, Any] = {"candidates_generated": 0, "candidates_evaluated": 0, "top_candidate": None, "errors": []}

    try:
        from .. import data_collector
        from ..research.hypothesis_ledger import default_db_path, record_result

        symbols = sorted({symbol for _, symbol in pairs})
        resume_state = checkpoint.load_checkpoint(config, AGENT_NAME) or {}
        completed_pairs = {tuple(p) for p in resume_state.get("completed_pairs", [])}

        price_history = data_collector.fetch_all_price_history(symbols, config, logger)

        scorecards: list[CandidateScorecard] = []
        for strategy_name, symbol in pairs:
            key = (strategy_name, symbol)
            if key in completed_pairs:
                logger.info("Strategy Lab: (%s, %s) already completed this cycle (resuming), skipping.", strategy_name, symbol)
                continue

            candidates = generate_candidates(strategy_name, symbol, window_grid)
            summary["candidates_generated"] += len(candidates)

            for candidate in candidates:
                try:
                    scorecard = evaluate_candidate(candidate, price_history, config, logger)
                except Exception as exc:  # noqa: BLE001 - one bad candidate must never stop the rest
                    summary["errors"].append(f"candidate {candidate.hypothesis_id} raised: {exc}")
                    continue

                scorecards.append(scorecard)
                summary["candidates_evaluated"] += 1
                if scorecard.single_window_stats is not None:
                    record_result(
                        default_db_path(config),
                        {
                            "hypothesis_id": candidate.hypothesis_id, "description": candidate.description,
                            "strategy_name": candidate.strategy_name, "symbol": candidate.symbol,
                            "data_provenance": candidate.data_provenance, "config_overrides": candidate.config_overrides,
                            "stats": scorecard.single_window_stats, "trade_count": scorecard.single_window_stats.get("total_trades", 0),
                        },
                    )

            completed_pairs.add(key)
            checkpoint.save_checkpoint(config, AGENT_NAME, {"completed_pairs": [list(p) for p in completed_pairs]})

        checkpoint.clear_checkpoint(config, AGENT_NAME)

        ranked = rank_candidates(scorecards)
        if ranked:
            top = ranked[0]
            summary["top_candidate"] = {
                "hypothesis_id": top.hypothesis.hypothesis_id, "strategy_name": top.hypothesis.strategy_name,
                "symbol": top.hypothesis.symbol, "verdict": top.verdict, "verdict_reason": top.verdict_reason,
            }

    except Exception as exc:  # noqa: BLE001 - this agent's top-level isolation boundary
        logger.error("Strategy Lab: run_lab_cycle failed: %s", exc, exc_info=True)
        summary["errors"].append(str(exc))
        registry.finish_run(config, AGENT_NAME, registry.STATUS_FAILED, error=str(exc), extra=summary, now=now)
        return summary

    registry.finish_run(
        config, AGENT_NAME, registry.STATUS_SUCCESS,
        summary=f"{summary['candidates_evaluated']} candidate(s) evaluated, top: {(summary['top_candidate'] or {}).get('hypothesis_id')}",
        extra=summary, now=now,
    )
    return summary


def main() -> int:
    """`python -m src.agents.strategy_lab` - runs one full lab cycle.
    Has its own singleton lock, independent of every other process in
    this codebase. Never places, suggests, or triggers a trade - see
    module docstring."""
    from ..execution.process_lock import ProcessAlreadyRunningError, acquire_singleton_lock
    from ..utils import load_config, load_env, resolve_path, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="strategy_lab.log")

    lock_path = resolve_path(config.get("data", {}).get("journal_dir", "data/journal")) / "agents" / "strategy_lab.lock"
    try:
        _lock_handle = acquire_singleton_lock(lock_path, "strategy_lab")
    except ProcessAlreadyRunningError as exc:
        print(f"Strategy Lab already running: {exc}")
        return 1

    summary = run_lab_cycle(config, logger)
    print(f"Strategy Lab: {summary}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
