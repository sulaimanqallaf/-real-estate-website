"""Big Money feature aggregation - combines raw institutional/insider/options-
flow/context facts into transparent, component-scored context. This module is
REPORTING/CONTEXT ONLY (see `use_for_ranking` in config): it never bypasses,
and is never allowed to bypass, the existing signal label, individual risk
manager, market regime filter, portfolio risk manager, or the aggressive-mode
gate. See README "Big Money Data Engine" for the full rationale.

Component scores are all on a -1.0 (bearish context) to +1.0 (bullish
context) scale, `None` when the underlying data is unavailable - NEVER
silently coerced to 0.0. 0.0 means "the data says neutral"; `None` means "we
don't know." `data_quality_score` (0.0-1.0) tells you how many of the
possible components actually had data, so a composite built from one thin
component can be told apart from one built from four solid ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

COMPONENT_INSTITUTIONAL = "institutional_accumulation_score"
COMPONENT_INSIDER = "insider_score"
COMPONENT_OPTIONS_FLOW = "options_flow_score"
COMPONENT_RELATIVE_VOLUME = "relative_volume_score"
COMPONENT_SECTOR_FLOW = "sector_flow_score"

ALL_COMPONENTS = (
    COMPONENT_INSTITUTIONAL,
    COMPONENT_INSIDER,
    COMPONENT_OPTIONS_FLOW,
    COMPONENT_RELATIVE_VOLUME,
    COMPONENT_SECTOR_FLOW,
)


@dataclass(frozen=True)
class BigMoneyScore:
    ticker: str
    components: dict[str, float | None]
    composite_score: float | None
    data_quality_score: float
    notes: list[str] = field(default_factory=list)

    @property
    def has_any_data(self) -> bool:
        return any(v is not None for v in self.components.values())


def score_institutional_accumulation(institutional_facts: dict[str, Any] | None) -> float | None:
    """Raw facts (see sec_provider.institutional_raw_facts) -> a transparent
    -1..+1 score. Deliberately simple and inspectable: net "buy-side" manager
    actions (new + increased) minus "sell-side" ones (reduced + exited), as a
    fraction of managers who touched this ticker at all. `None` (never 0.0)
    when there's no 13F data for this ticker at all - a 0.0 here would
    silently claim "institutions are neutral on this," when the truth is "we
    have no 13F evidence either way." """
    if institutional_facts is None or not institutional_facts.get("has_data"):
        return None
    total = (
        institutional_facts["new_positions"]
        + institutional_facts["increased_positions"]
        + institutional_facts["reduced_positions"]
        + institutional_facts["exited_positions"]
    )
    if total == 0:
        return None
    buy_side = institutional_facts["new_positions"] + institutional_facts["increased_positions"]
    sell_side = institutional_facts["reduced_positions"] + institutional_facts["exited_positions"]
    return round((buy_side - sell_side) / total, 4)


def score_insider_activity(insider_features: dict[str, Any] | None) -> float | None:
    """`None` when there's no Form 4 open-market activity in the lookback
    window at all - distinct from a genuine 0.0 (equal buy/sell $ value)."""
    if insider_features is None or not insider_features.get("has_data"):
        return None
    buy = insider_features["insider_buy_value_30d"]
    sell = insider_features["insider_sell_value_30d"]
    total = buy + sell
    if total == 0:
        return None
    score = (buy - sell) / total
    if insider_features.get("cluster_buying"):
        score = min(1.0, score + 0.15)
    return round(score, 4)


def score_options_flow(flow_events: list[Any] | None) -> float | None:
    """`None` whenever no options-flow provider is configured/returned data -
    see `data_providers/options_flow_provider.py`. This is intentionally the
    component most likely to be `None` today, since only the mock provider
    exists (see README)."""
    if not flow_events:
        return None
    call_premium = sum(e.premium or 0.0 for e in flow_events if e.call_put == "call")
    put_premium = sum(e.premium or 0.0 for e in flow_events if e.call_put == "put")
    total = call_premium + put_premium
    if total == 0:
        return None
    return round((call_premium - put_premium) / total, 4)


def score_relative_volume(relative_volume: float | None) -> float | None:
    """Reuses the already-computed `relative_volume` indicator (see
    `indicators.py`) - not a new data source, just folded into the Big Money
    composite with a bounded, transparent mapping: 1.0x average volume ->
    0.0; scales linearly, capped at +-1.0."""
    from .utils import is_nan

    if relative_volume is None or is_nan(relative_volume):
        return None
    return max(-1.0, min(1.0, round((relative_volume - 1.0), 4)))


def score_sector_flow(sector_avg_score: float | None) -> float | None:
    """Placeholder for a sector-wide flow signal (e.g. average institutional
    score across `sector_map` peers) - `None` until a caller actually
    supplies one; never fabricated here."""
    if sector_avg_score is None:
        return None
    return max(-1.0, min(1.0, round(sector_avg_score, 4)))


def compute_big_money_score(
    ticker: str,
    institutional_facts: dict[str, Any] | None = None,
    insider_features: dict[str, Any] | None = None,
    flow_events: list[Any] | None = None,
    relative_volume: float | None = None,
    sector_avg_score: float | None = None,
    weights: dict[str, float] | None = None,
) -> BigMoneyScore:
    """Transparent component scoring with an explicit breakdown - never a
    single opaque number. `weights` defaults to equal weight across whatever
    components actually have data (missing components are excluded from both
    the weighted sum AND the weight normalization, never treated as 0.0)."""
    default_weights = {
        COMPONENT_INSTITUTIONAL: 1.0,
        COMPONENT_INSIDER: 1.0,
        COMPONENT_OPTIONS_FLOW: 1.0,
        COMPONENT_RELATIVE_VOLUME: 0.5,
        COMPONENT_SECTOR_FLOW: 0.5,
    }
    weights = weights or default_weights

    components = {
        COMPONENT_INSTITUTIONAL: score_institutional_accumulation(institutional_facts),
        COMPONENT_INSIDER: score_insider_activity(insider_features),
        COMPONENT_OPTIONS_FLOW: score_options_flow(flow_events),
        COMPONENT_RELATIVE_VOLUME: score_relative_volume(relative_volume),
        COMPONENT_SECTOR_FLOW: score_sector_flow(sector_avg_score),
    }

    available = {k: v for k, v in components.items() if v is not None}
    data_quality_score = round(len(available) / len(ALL_COMPONENTS), 4)

    notes = []
    if not available:
        composite = None
        notes.append("No Big Money component data available - composite is Data Unavailable, not neutral.")
    else:
        total_weight = sum(weights.get(k, 0.0) for k in available)
        if total_weight <= 0:
            composite = None
            notes.append("All available components have zero configured weight.")
        else:
            composite = round(sum(available[k] * weights.get(k, 0.0) for k in available) / total_weight, 4)
        missing = [k for k in ALL_COMPONENTS if k not in available]
        if missing:
            notes.append(f"Missing components (excluded from composite, not zeroed): {', '.join(missing)}.")

    return BigMoneyScore(
        ticker=ticker,
        components=components,
        composite_score=composite,
        data_quality_score=data_quality_score,
        notes=notes,
    )


def apply_big_money_ranking_filter(
    ticker_results: list[dict[str, Any]],
    big_money_scores: dict[str, BigMoneyScore],
    config: dict[str, Any],
) -> None:
    """Optional, config-gated, additive-only use of the Big Money score as a
    STRICTER ranking/filtering signal - see `config.big_money.use_for_ranking`.
    Attaches `entry["big_money_score"]` to every entry for reporting
    regardless of this flag. When the flag is on, this may only ADD an
    informational note or a stricter internal ranking key - it must NEVER
    change `entry["label"]`, NEVER flip an Avoid to anything else, and NEVER
    touch `entry["best_risk_result"]`, `entry["regime_evaluation"]`, or
    `entry["portfolio_evaluation"]` - those remain the sole authority over
    whether something can become a Top Candidate. This function is called
    AFTER those gates already ran; it has no mechanism to reach backward into
    them, by design.
    """
    cfg = config.get("big_money", {})
    if not cfg.get("enabled", True):
        for entry in ticker_results:
            entry["big_money_score"] = None
        return

    for entry in ticker_results:
        score = big_money_scores.get(entry["symbol"])
        entry["big_money_score"] = score

    if not cfg.get("use_for_ranking", False):
        return

    # Additive-only, stricter-only: a low/negative composite score can add a
    # cautionary note, but can never promote an Avoid-labeled entry, and never
    # touches the fields the risk/regime/portfolio gates already decided.
    for entry in ticker_results:
        score = entry.get("big_money_score")
        if score is None or score.composite_score is None:
            continue
        if entry["label"] == "Avoid":
            continue  # never promoted - see docstring
        threshold = cfg.get("ranking_caution_threshold", -0.3)
        if score.composite_score <= threshold:
            entry.setdefault("big_money_notes", []).append(
                f"Big Money composite score {score.composite_score:.2f} is unusually weak for this candidate - "
                f"context only, does not change its risk/regime/portfolio approval."
            )
