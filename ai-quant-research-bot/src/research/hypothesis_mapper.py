"""Maps a `Paper`'s matched strategy archetype(s) into falsifiable,
testable `sandbox.ResearchHypothesis` objects (Sprint 4, "Autonomous
academic research" milestone, Priority 2: "...generate falsifiable
strategy hypotheses...").

**Falsifiable, concretely.** Each hypothesis names one specific,
already-implemented strategy (`backtester.STRATEGY_MODULES`), one
specific symbol, and a plain-English claim a REAL backtest either
supports or refutes - e.g. "default-parameter Mean Reversion produces
a positive total return on SPY over the configured lookback window."
`sandbox.run_hypothesis()`'s actual result (zero trades, a negative
return, or a positive one) is the falsification test itself - this
module makes no judgment call about whether the paper was "right."

**No NLP beyond simple keyword/number matching.** `research.papers.
matched_strategies()` does the keyword matching; `_extract_window()`
below optionally pulls an explicit "N-day" mention out of the title/
abstract and maps it to that strategy's own documented window
parameter - and ONLY when that parameter exists
(`_WINDOW_OVERRIDE_KEY`). No window found, or no mapping for that
strategy, means no override at all (the strategy's own defaults),
never a guessed number.
"""

from __future__ import annotations

import re

from .papers import Paper, matched_strategies
from .sandbox import ResearchHypothesis

_WINDOW_PATTERN = re.compile(r"(\d{1,3})[- ]day", re.IGNORECASE)

# Which config key a "N-day" mention maps to, per strategy - see
# config/settings.yaml's strategies.* section. Trend Following has no
# single window knob exposed this way, so it's deliberately absent.
_WINDOW_OVERRIDE_KEY: dict[str, str] = {
    "Mean Reversion": "ma_window",
    "Momentum Breakout": "breakout_window",
}


def _extract_window(text: str) -> int | None:
    match = _WINDOW_PATTERN.search(text)
    if not match:
        return None
    value = int(match.group(1))
    return value if 2 <= value <= 252 else None  # sanity bound - a "1-day" or "999-day" mention is not a real window suggestion


def generate_hypotheses(paper: Paper, symbols: list[str]) -> list[ResearchHypothesis]:
    """One hypothesis per (matched strategy, symbol) pair - `symbols`
    is the caller's choice of which liquid instruments to test against
    (this module has no opinion on universe selection). Returns `[]`
    when the paper matches no known strategy archetype at all - never
    a fabricated, unrelated hypothesis."""
    text = f"{paper.title} {paper.abstract or ''}"
    strategies = matched_strategies(text)
    if not strategies or not symbols:
        return []

    window = _extract_window(text)
    key = paper_hypothesis_key(paper)

    hypotheses: list[ResearchHypothesis] = []
    for strategy_name in strategies:
        overrides: dict[str, dict[str, int]] = {}
        override_key = _WINDOW_OVERRIDE_KEY.get(strategy_name)
        if window is not None and override_key is not None:
            overrides = {"strategies": {_strategy_config_section(strategy_name): {override_key: window}}}

        for symbol in symbols:
            description = (
                f"Paper '{paper.title}' (sources: {', '.join(paper.sources)}) suggests {strategy_name} dynamics. "
                f"Hypothesis: {strategy_name}"
                + (f" with a {window}-day window" if window is not None and override_key is not None else " (default parameters)")
                + f" produces a positive total return on {symbol} over the configured lookback window. "
                "Falsified if the real backtest shows zero trades, a non-positive total return, or a negative Sharpe ratio."
            )
            hypotheses.append(
                ResearchHypothesis(
                    hypothesis_id=f"{key}_{_strategy_config_section(strategy_name)}_{symbol}",
                    description=description,
                    strategy_name=strategy_name,
                    symbol=symbol,
                    config_overrides=overrides,
                    data_provenance="real_market_data",
                )
            )
    return hypotheses


def paper_hypothesis_key(paper: Paper) -> str:
    """A short, stable id fragment derived from the paper's own key
    (reuses `paper_memory.paper_key()`'s identity logic) - lets a
    human trace a hypothesis_id in the ledger straight back to the
    paper that generated it."""
    from .paper_memory import paper_key

    raw = paper_key(paper).replace(":", "_")
    return re.sub(r"[^a-z0-9_]", "", raw.lower())[:48]


def _strategy_config_section(strategy_name: str) -> str:
    return {"Mean Reversion": "mean_reversion", "Momentum Breakout": "momentum_breakout", "Trend Following": "trend_following"}[strategy_name]
