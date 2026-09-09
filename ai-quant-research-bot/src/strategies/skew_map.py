"""Classify a ticker's options-skew / big-money positioning.

Definitions used here (documented since the spec names the four buckets but not the
exact sign convention):
  - "Calls bid"  == negative skew (OTM call IV richer than OTM put IV, relative to ATM).
  - "Puts bid"   == positive skew (OTM put IV richer than OTM call IV, relative to ATM).
  - direction    == sign of the trailing 1-month return.

    1M return   |  calls bid (skew < -threshold)  |  puts bid (skew > +threshold)
    ------------+---------------------------------+-------------------------------
    down        |  Contrarian Bid                  |  Fear
    up          |  Chase                            |  Hedged Rally

This is a watchlist layer only - it never gates or forces a trade.
"""

from __future__ import annotations

from typing import Any

NEUTRAL = "Neutral / Unclear"
DATA_UNAVAILABLE = "Data Unavailable"

CONTRARIAN_BID = "Contrarian Bid"
CHASE = "Chase"
HEDGED_RALLY = "Hedged Rally"
FEAR = "Fear"


def classify_skew(return_1m_pct: float | None, skew: float | None, config: dict[str, Any]) -> str:
    if skew is None or return_1m_pct is None or return_1m_pct != return_1m_pct:  # NaN check
        return DATA_UNAVAILABLE

    threshold = config["strategies"]["skew"]["skew_threshold"]

    calls_bid = skew < -threshold
    puts_bid = skew > threshold
    is_up = return_1m_pct > 0
    is_down = return_1m_pct < 0

    if is_down and calls_bid:
        return CONTRARIAN_BID
    if is_up and calls_bid:
        return CHASE
    if is_up and puts_bid:
        return HEDGED_RALLY
    if is_down and puts_bid:
        return FEAR
    return NEUTRAL


def is_favorable(classification: str, config: dict[str, Any]) -> bool:
    return classification in config["strategies"]["skew"]["favorable_skew_labels"]
