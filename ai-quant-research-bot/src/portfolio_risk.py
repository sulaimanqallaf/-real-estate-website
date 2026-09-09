"""Portfolio Risk Engine.

Sits AFTER the individual Risk Manager (risk_manager.py, unchanged - it still
evaluates one trade in isolation) and the Market Regime filter, and BEFORE a
candidate becomes a Top Candidate:

    Signal -> Individual Risk Manager -> Market Regime Filter
           -> Portfolio Risk Manager -> Top Candidate -> Telegram Approval -> Paper Trade

Portfolio state is derived ONLY from rows with status == "OPEN" in
paper_trades.csv - a closed trade (TARGET_HIT/STOPPED/TIME_EXIT/CANCELLED/
REJECTED-anything) never counts toward exposure, by construction (it's simply
never in the DataFrame this module filters to).

This module can only keep a candidate's already-regime-adjusted size the same,
shrink it further, or reject it outright. It never increases a position beyond
what was handed to it - every resize helper here truncates toward fewer shares,
never more.

Known V1 simplification: when several tickers all qualify as candidates on the
same day, each one is evaluated against a *running* portfolio state that
includes every ACCEPT/ACCEPT_WITH_REDUCED_SIZE decision made earlier in that
same batch (processed score-descending, so stronger candidates get first claim
on the risk budget) - see main.py's run_portfolio_and_regime_pipeline call. It
does NOT attempt game-theoretic joint optimization across the whole batch;
sequential greedy allocation by score is a reasonable, explainable V1 answer to
"could today's candidates collectively overshoot a limit," not a claim of
optimality.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

# Guards floor() against floating-point representation error (e.g. 0.03 - 0.025
# == 0.0049999999999999975 in IEEE 754, not exactly 0.005) silently under-flooring
# a share count by one. Small enough to never round a genuinely-too-large value
# down into a falsely-viable one.
_FLOOR_EPSILON = 1e-9


def _floor_shares(value: float) -> int:
    return math.floor(value + _FLOOR_EPSILON)


def sector_for_ticker(ticker: str, config: dict[str, Any]) -> str:
    """A ticker missing from sector_map is its own single-ticker sector (its
    symbol) - concentration math never crashes on an unmapped ticker, it just
    can't be "concentrated" with any other ticker by sector."""
    return config.get("sector_map", {}).get(ticker, ticker)


def find_overlap_group(ticker: str, config: dict[str, Any]) -> str | None:
    for group_name, members in config["portfolio_risk"].get("overlap_groups", {}).items():
        if ticker in members:
            return group_name
    return None


def compute_daily_return_correlation(
    price_df_a: pd.DataFrame | None, price_df_b: pd.DataFrame | None, config: dict[str, Any]
) -> float | None:
    """Pearson correlation of trailing daily % returns. Returns None - never a
    fabricated 0.0 or any other guessed value - when either series is missing or
    there isn't enough overlapping history to trust the number."""
    cfg = config["portfolio_risk"]
    if price_df_a is None or price_df_b is None:
        return None

    lookback = cfg["correlation_lookback_days"]
    returns_a = price_df_a["close"].pct_change().tail(lookback)
    returns_b = price_df_b["close"].pct_change().tail(lookback)

    aligned = pd.concat([returns_a, returns_b], axis=1, join="inner").dropna()
    if len(aligned) < cfg["correlation_min_periods"]:
        return None

    corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    if corr is None or corr != corr:  # NaN-safe
        return None
    return float(corr)


_EMPTY_STATE = {
    "num_open_positions": 0,
    "total_deployed_capital": 0.0,
    "total_open_risk_dollars": 0.0,
    "total_open_risk_fraction": 0.0,
    "gross_exposure_fraction": 0.0,
    "exposure_by_ticker": {},
    "exposure_by_strategy": {},
    "exposure_by_mode": {},
    "exposure_by_sector": {},
    "tickers_open": [],
    "largest_position_fraction": 0.0,
    "largest_sector": None,
    "largest_sector_fraction": 0.0,
}


def compute_portfolio_state(open_trades_df: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    """Portfolio state derived purely from OPEN rows. All *_fraction values are
    plain 0.0-1.0 ratios of account_equity, directly comparable against this
    module's config thresholds (also 0.0-1.0 fractions, e.g. 0.03 = 3%) - never
    pre-multiplied by 100. Multiply by 100 only when formatting for display."""
    if open_trades_df is None or open_trades_df.empty:
        return dict(_EMPTY_STATE, exposure_by_ticker={}, exposure_by_strategy={}, exposure_by_mode={}, exposure_by_sector={}, tickers_open=[])

    open_trades = open_trades_df[open_trades_df["status"] == "OPEN"]
    if open_trades.empty:
        return dict(_EMPTY_STATE, exposure_by_ticker={}, exposure_by_strategy={}, exposure_by_mode={}, exposure_by_sector={}, tickers_open=[])

    account_equity = config["risk"]["account_equity"]
    entry_price = open_trades["entry_price"].astype(float)
    stop_loss = open_trades["stop_loss"].astype(float)
    position_size = open_trades["position_size"].astype(float)

    deployed = entry_price * position_size
    risk_to_stop = (entry_price - stop_loss) * position_size
    sectors = open_trades["ticker"].apply(lambda t: sector_for_ticker(t, config))

    exposure_by_ticker = deployed.groupby(open_trades["ticker"]).sum()
    exposure_by_strategy = deployed.groupby(open_trades["strategy"]).sum()
    exposure_by_mode = deployed.groupby(open_trades["mode"]).sum()
    exposure_by_sector = deployed.groupby(sectors).sum()

    total_deployed = float(deployed.sum())
    total_risk = float(risk_to_stop.sum())
    largest_position = float(deployed.max())

    largest_sector = None
    largest_sector_value = 0.0
    if len(exposure_by_sector):
        largest_sector = exposure_by_sector.idxmax()
        largest_sector_value = float(exposure_by_sector.max())

    def _frac(value: float) -> float:
        return value / account_equity if account_equity else 0.0

    return {
        "num_open_positions": len(open_trades),
        "total_deployed_capital": round(total_deployed, 2),
        "total_open_risk_dollars": round(total_risk, 2),
        "total_open_risk_fraction": _frac(total_risk),
        "gross_exposure_fraction": _frac(total_deployed),
        "exposure_by_ticker": {k: round(v, 2) for k, v in exposure_by_ticker.items()},
        "exposure_by_strategy": {k: round(v, 2) for k, v in exposure_by_strategy.items()},
        "exposure_by_mode": {k: round(v, 2) for k, v in exposure_by_mode.items()},
        "exposure_by_sector": {k: round(v, 2) for k, v in exposure_by_sector.items()},
        "tickers_open": list(open_trades["ticker"]),
        "largest_position_fraction": _frac(largest_position),
        "largest_sector": largest_sector,
        "largest_sector_fraction": _frac(largest_sector_value),
    }


def _resize(position: dict[str, Any], new_shares: int) -> dict[str, Any]:
    """Rebuild a position proposal at a (smaller) share count. entry/stop/target
    (and therefore risk_reward) never change - only shares/dollar_risk/position_value."""
    resized = dict(position)
    resized["shares"] = max(0, new_shares)
    resized["dollar_risk"] = round(resized["shares"] * (position["entry"] - position["stop_loss"]), 2)
    resized["position_value"] = round(resized["shares"] * position["entry"], 2)
    return resized


def _min_viable(config: dict[str, Any]) -> int:
    return config["portfolio_risk"].get("min_viable_shares", 1)


def evaluate_portfolio_candidate(
    proposal: dict[str, Any],
    state: dict[str, Any],
    price_data: dict[str, pd.DataFrame],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate one candidate (already individual-risk- and regime-adjusted)
    against a given portfolio `state` (see compute_portfolio_state). Returns a
    dict with `decision` ("ACCEPT" | "ACCEPT_WITH_REDUCED_SIZE" | "REJECT"),
    `position` (the final sizing, or None if rejected), `original_position`,
    before/after exposure figures, correlation/overlap notes, and human-readable
    `rejection_reasons` / `warnings`.
    """
    cfg = config["portfolio_risk"]
    account_equity = config["risk"]["account_equity"]
    ticker = proposal["ticker"]
    original = dict(proposal)
    warnings: list[str] = []

    def _reject(reasons: list[str]) -> dict[str, Any]:
        return {
            "decision": "REJECT",
            "position": None,
            "original_position": original,
            "sector": sector_for_ticker(ticker, config),
            "total_open_risk_before": state["total_open_risk_fraction"],
            "total_open_risk_after": state["total_open_risk_fraction"],
            "sector_exposure_before": state["exposure_by_sector"].get(sector_for_ticker(ticker, config), 0.0) / account_equity if account_equity else 0.0,
            "sector_exposure_after": state["exposure_by_sector"].get(sector_for_ticker(ticker, config), 0.0) / account_equity if account_equity else 0.0,
            "correlation_notes": [],
            "overlap_group": find_overlap_group(ticker, config),
            "rejection_reasons": reasons,
            "warnings": warnings,
        }

    if not cfg.get("enabled", True):
        return {
            "decision": "ACCEPT",
            "position": original,
            "original_position": original,
            "sector": sector_for_ticker(ticker, config),
            "total_open_risk_before": state["total_open_risk_fraction"],
            "total_open_risk_after": state["total_open_risk_fraction"],
            "sector_exposure_before": 0.0,
            "sector_exposure_after": 0.0,
            "correlation_notes": [],
            "overlap_group": None,
            "rejection_reasons": [],
            "warnings": [],
        }

    # 1. Max open positions.
    if state["num_open_positions"] >= cfg["max_open_positions"]:
        return _reject(
            [f"Portfolio blocked: max open positions reached ({state['num_open_positions']}/{cfg['max_open_positions']})"]
        )

    working = dict(proposal)

    # 2. Total open risk-to-stop budget.
    candidate_risk_fraction = (
        (working["entry"] - working["stop_loss"]) * working["shares"] / account_equity if account_equity else 0.0
    )
    max_total_risk = cfg["max_total_open_risk_pct"]
    if state["total_open_risk_fraction"] + candidate_risk_fraction > max_total_risk:
        remaining_budget = max(0.0, max_total_risk - state["total_open_risk_fraction"]) * account_equity
        risk_per_share = working["entry"] - working["stop_loss"]
        new_shares = _floor_shares(remaining_budget / risk_per_share) if risk_per_share > 0 else 0
        if new_shares < _min_viable(config):
            return _reject(
                [
                    f"Portfolio blocked: total open risk would exceed {max_total_risk * 100:.1f}% "
                    f"(currently {state['total_open_risk_fraction'] * 100:.2f}%, remaining budget too small "
                    f"for a viable position)"
                ]
            )
        working = _resize(working, new_shares)
        warnings.append(
            f"Reduced from {original['shares']} to {working['shares']} shares to stay within the "
            f"{max_total_risk * 100:.1f}% total open-risk budget."
        )

    # 3. Single-position notional cap.
    max_single = cfg["max_single_position_pct"]
    position_fraction = working["position_value"] / account_equity if account_equity else 0.0
    if position_fraction > max_single:
        max_value = max_single * account_equity
        new_shares = _floor_shares(max_value / working["entry"]) if working["entry"] > 0 else 0
        if new_shares < _min_viable(config):
            return _reject(
                [f"Portfolio blocked: position would exceed the max single-position size ({max_single * 100:.0f}% of equity)"]
            )
        working = _resize(working, new_shares)
        warnings.append(f"Reduced to stay within the max single-position cap ({max_single * 100:.0f}% of equity).")

    # 4. Sector concentration.
    sector = sector_for_ticker(ticker, config)
    sector_before_dollars = state["exposure_by_sector"].get(sector, 0.0)
    max_sector = cfg["max_sector_exposure_pct"]
    sector_after_fraction = (sector_before_dollars + working["position_value"]) / account_equity if account_equity else 0.0
    if sector_after_fraction > max_sector:
        max_sector_dollars = max_sector * account_equity - sector_before_dollars
        new_shares = _floor_shares(max_sector_dollars / working["entry"]) if working["entry"] > 0 and max_sector_dollars > 0 else 0
        if new_shares < _min_viable(config):
            return _reject(
                [
                    f"Portfolio blocked: '{sector}' exposure would rise to "
                    f"{sector_after_fraction * 100:.0f}% (limit {max_sector * 100:.0f}%)"
                ]
            )
        working = _resize(working, new_shares)
        sector_after_fraction = (sector_before_dollars + working["position_value"]) / account_equity if account_equity else 0.0
        warnings.append(
            f"Reduced due to '{sector}' sector concentration (would otherwise reach "
            f"{(sector_before_dollars + original['position_value']) / account_equity * 100:.0f}% vs. the "
            f"{max_sector * 100:.0f}% limit)."
        )

    # 5. Overlap groups - flagged regardless of whether correlation data exists.
    overlap_group = find_overlap_group(ticker, config)
    overlap_tickers = []
    if overlap_group:
        overlap_tickers = [t for t in state["tickers_open"] if t != ticker and find_overlap_group(t, config) == overlap_group]
        if overlap_tickers:
            warnings.append(
                f"Overlap warning: {ticker} shares the '{overlap_group}' group with already-open "
                f"{', '.join(sorted(set(overlap_tickers)))}."
            )

    # 6. Historical correlation, plus overlap-group membership counted as an
    # automatic "highly correlated" hit even when price-based correlation can't
    # be computed - obvious overlap is itself evidence of correlation.
    correlation_notes: list[str] = []
    highly_correlated_tickers: set[str] = set(overlap_tickers)
    for open_ticker in set(state["tickers_open"]):
        if open_ticker == ticker:
            continue
        corr = compute_daily_return_correlation(price_data.get(ticker), price_data.get(open_ticker), config)
        if corr is None:
            correlation_notes.append(f"{open_ticker}: correlation Data Unavailable")
            continue
        correlation_notes.append(f"{open_ticker}: correlation {corr:.2f}")
        if abs(corr) >= cfg["high_correlation_threshold"]:
            highly_correlated_tickers.add(open_ticker)

    if len(highly_correlated_tickers) >= cfg["max_correlated_positions"]:
        new_shares = working["shares"] // 2
        if new_shares < _min_viable(config):
            return _reject(
                [f"Portfolio blocked: highly correlated with {len(highly_correlated_tickers)} existing position(s)."]
            )
        working = _resize(working, new_shares)
        warnings.append(f"Reduced 50% due to high correlation with {len(highly_correlated_tickers)} existing position(s).")

    decision = "ACCEPT" if working["shares"] == original["shares"] else "ACCEPT_WITH_REDUCED_SIZE"

    return {
        "decision": decision,
        "position": working,
        "original_position": original,
        "sector": sector,
        "total_open_risk_before": state["total_open_risk_fraction"],
        "total_open_risk_after": state["total_open_risk_fraction"] + (
            (working["entry"] - working["stop_loss"]) * working["shares"] / account_equity if account_equity else 0.0
        ),
        "sector_exposure_before": sector_before_dollars / account_equity if account_equity else 0.0,
        "sector_exposure_after": sector_after_fraction,
        "correlation_notes": correlation_notes,
        "overlap_group": overlap_group,
        "rejection_reasons": [],
        "warnings": warnings,
    }


def apply_acceptance_to_state(state: dict[str, Any], position: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Provisionally fold an accepted candidate into a portfolio state, for
    sequential same-batch evaluation (see module docstring's "Known V1
    simplification"). Returns a NEW state dict - the input is never mutated."""
    account_equity = config["risk"]["account_equity"]
    ticker = position["ticker"]
    sector = sector_for_ticker(ticker, config)

    deployed = position["position_value"]
    risk_dollars = (position["entry"] - position["stop_loss"]) * position["shares"]

    new_state = {**state}
    new_state["num_open_positions"] = state["num_open_positions"] + 1
    new_state["total_deployed_capital"] = round(state["total_deployed_capital"] + deployed, 2)
    new_state["total_open_risk_dollars"] = round(state["total_open_risk_dollars"] + risk_dollars, 2)
    new_state["total_open_risk_fraction"] = new_state["total_open_risk_dollars"] / account_equity if account_equity else 0.0
    new_state["gross_exposure_fraction"] = new_state["total_deployed_capital"] / account_equity if account_equity else 0.0

    new_state["exposure_by_sector"] = dict(state["exposure_by_sector"])
    new_state["exposure_by_sector"][sector] = round(new_state["exposure_by_sector"].get(sector, 0.0) + deployed, 2)

    new_state["exposure_by_ticker"] = dict(state["exposure_by_ticker"])
    new_state["exposure_by_ticker"][ticker] = round(new_state["exposure_by_ticker"].get(ticker, 0.0) + deployed, 2)

    new_state["tickers_open"] = state["tickers_open"] + [ticker]

    if new_state["exposure_by_sector"]:
        largest_sector = max(new_state["exposure_by_sector"], key=new_state["exposure_by_sector"].get)
        new_state["largest_sector"] = largest_sector
        new_state["largest_sector_fraction"] = (
            new_state["exposure_by_sector"][largest_sector] / account_equity if account_equity else 0.0
        )

    new_state["largest_position_fraction"] = max(
        state["largest_position_fraction"], deployed / account_equity if account_equity else 0.0
    )

    return new_state


def build_proposal(ticker: str, risk_result: dict[str, Any], size_multiplier: float = 1.0) -> dict[str, Any]:
    """Build a position proposal from an individual-risk-approved result, cut by
    `size_multiplier` (regime-wide x per-strategy-status, both <= 1.0 - see
    market_regime.py). entry/stop/target/risk_reward/upside/downside are price-
    based ratios that don't change with share count, so they carry over
    unchanged; only shares/dollar_risk/position_value are recomputed."""
    shares = max(0, math.floor(risk_result["shares"] * size_multiplier + _FLOOR_EPSILON))
    entry = risk_result["entry"]
    stop_loss = risk_result["stop_loss"]
    return {
        "ticker": ticker,
        "strategy": risk_result["strategy"],
        "tradeable": True,
        "blocked_reasons": [],
        "entry": entry,
        "stop_loss": stop_loss,
        "target": risk_result["target"],
        "expected_upside_pct": risk_result.get("expected_upside_pct"),
        "expected_downside_pct": risk_result.get("expected_downside_pct"),
        "risk_reward": risk_result["risk_reward"],
        "shares": shares,
        "dollar_risk": round(shares * (entry - stop_loss), 2),
        "position_value": round(shares * entry, 2),
    }


def _rejected_before_portfolio(ticker: str, risk_result: dict[str, Any], reasons: list[str], config: dict[str, Any]) -> dict[str, Any]:
    """Same return shape as evaluate_portfolio_candidate's REJECT case, for a
    candidate blocked by market regime before portfolio risk even runs - so
    downstream code (select_top_candidates, report formatting) can treat every
    rejection uniformly regardless of which stage produced it."""
    original = {
        "ticker": ticker, "strategy": risk_result["strategy"], "entry": risk_result["entry"],
        "stop_loss": risk_result["stop_loss"], "target": risk_result["target"], "shares": risk_result["shares"],
        "dollar_risk": risk_result["dollar_risk"], "position_value": risk_result["position_value"],
        "risk_reward": risk_result["risk_reward"],
    }
    return {
        "decision": "REJECT",
        "position": None,
        "original_position": original,
        "sector": sector_for_ticker(ticker, config),
        "total_open_risk_before": None,
        "total_open_risk_after": None,
        "sector_exposure_before": None,
        "sector_exposure_after": None,
        "correlation_notes": [],
        "overlap_group": find_overlap_group(ticker, config),
        "rejection_reasons": reasons,
        "warnings": [],
    }


def run_regime_and_portfolio_pipeline(
    ticker_results: list[dict[str, Any]],
    regime: Any,
    open_trades_df: pd.DataFrame,
    price_data: dict[str, pd.DataFrame],
    config: dict[str, Any],
    logger: Any,
) -> None:
    """Mutate every entry in `ticker_results` in place, attaching
    entry["regime_evaluation"] and entry["portfolio_evaluation"]. Only entries
    that already pass risk_manager.passes_universal_gates() (individual risk +
    non-Avoid label + aggressive-enabled-if-needed) are evaluated at all -
    everything else gets both fields set to None (never evaluated, not the same
    as "evaluated and rejected"). Processed score-descending so stronger
    candidates claim the shared risk budget first within this one run - see the
    module docstring's "Known V1 simplification".
    """
    from . import market_regime, risk_manager

    for entry in ticker_results:
        entry["regime_evaluation"] = None
        entry["portfolio_evaluation"] = None

    candidates = [e for e in ticker_results if risk_manager.passes_universal_gates(e, config)]
    candidates.sort(key=lambda e: e["score"], reverse=True)

    state = compute_portfolio_state(open_trades_df, config)
    aggressive_enabled = config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"]

    for entry in candidates:
        risk_result = entry["best_risk_result"]
        ticker = entry["symbol"]
        strategy_key = market_regime.strategy_key_for(risk_result["strategy"])

        if strategy_key is None:
            # Not one of the four known strategies (shouldn't happen) - never
            # silently drop a candidate over it, just don't apply regime rules.
            admission = {"status": "allowed", "blocked": False, "size_multiplier": 1.0, "note": "Unrecognized strategy - regime rules not applied."}
        else:
            admission = market_regime.evaluate_regime_admission(strategy_key, regime.primary, config, aggressive_enabled)

        regime_multiplier = config["market_regime"]["position_multipliers"].get(regime.primary, 1.0)
        combined_multiplier = regime_multiplier * admission["size_multiplier"]

        entry["regime_evaluation"] = {
            "regime": regime.primary,
            "strategy_key": strategy_key,
            "status": admission["status"],
            "blocked": admission["blocked"],
            "regime_multiplier": regime_multiplier,
            "status_multiplier": admission["size_multiplier"],
            "combined_multiplier": combined_multiplier,
            "note": admission["note"],
        }

        if admission["blocked"]:
            entry["portfolio_evaluation"] = _rejected_before_portfolio(
                ticker, risk_result, [f"Blocked by market regime: {strategy_key} is disabled in {regime.primary}."], config
            )
            logger.info("Regime blocked %s: %s disabled in %s", ticker, strategy_key, regime.primary)
            continue

        if not market_regime.passes_regime_score_gate(entry["score"], regime.primary, config):
            threshold = config["market_regime"]["score_thresholds"].get(regime.primary)
            entry["portfolio_evaluation"] = _rejected_before_portfolio(
                ticker, risk_result,
                [f"Blocked by market regime: score {entry['score']} is below the {threshold} required in {regime.primary}."],
                config,
            )
            logger.info("Regime score gate blocked %s in %s (score %s < %s)", ticker, regime.primary, entry["score"], threshold)
            continue

        proposal = build_proposal(ticker, risk_result, combined_multiplier)

        if proposal["shares"] < _min_viable(config):
            entry["portfolio_evaluation"] = _rejected_before_portfolio(
                ticker, risk_result,
                [f"Blocked by market regime: regime-adjusted position is not viable (0 shares) in {regime.primary}."],
                config,
            )
            continue

        portfolio_result = evaluate_portfolio_candidate(proposal, state, price_data, config)
        entry["portfolio_evaluation"] = portfolio_result

        if portfolio_result["decision"] != "REJECT":
            state = apply_acceptance_to_state(state, portfolio_result["position"], config)
