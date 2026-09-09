"""Options skew / "big money" positioning engine.

Skew = (OTM put IV - OTM call IV) / ATM IV

  Positive skew -> puts are relatively expensive -> downside protection is bid.
  Negative skew -> calls are relatively expensive -> upside is bid.

This is a WATCHLIST layer only, per the spec - it never gates or forces a trade by
itself. It only feeds a classification label and one scoring bonus.

Data source note: yfinance's option chain `impliedVolatility` field is Yahoo's own
calculation and is frequently 0.0, NaN, or stale on thin/illiquid strikes - this is a
free-data limitation, not a bug in this module. When usable IV can't be found for a
symbol/expiration, this module returns skew=None with a clear reason rather than
guessing. To move to a paid provider (Polygon, Tradier, ORATS, Intrinio, CBOE
LiveVol), replace `fetch_option_chain_snapshot` with an implementation that returns
the same `OptionChainSnapshot` shape - nothing downstream needs to change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from . import data_collector


@dataclass
class OptionChainSnapshot:
    symbol: str
    expiration: str | None
    atm_iv: float | None
    otm_call_iv: float | None
    otm_put_iv: float | None
    skew: float | None
    reason: str | None = None  # populated only when skew is None


def _pick_expiration(expirations: list[str], min_days: int, max_days: int) -> str | None:
    today = datetime.now(timezone.utc).date()
    candidates = []
    for exp_str in expirations:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        days_out = (exp_date - today).days
        if min_days <= days_out <= max_days:
            candidates.append((abs(days_out - (min_days + max_days) / 2), exp_str))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _usable_iv_rows(chain: pd.DataFrame) -> pd.DataFrame:
    """Filter to rows with a plausible, non-zero implied volatility."""
    if "impliedVolatility" not in chain.columns or "strike" not in chain.columns:
        return chain.iloc[0:0]
    return chain[(chain["impliedVolatility"] > 0.0) & chain["impliedVolatility"].notna()]


def _nearest_strike_iv(chain: pd.DataFrame, target_strike: float) -> float | None:
    usable = _usable_iv_rows(chain)
    if usable.empty:
        return None
    idx = (usable["strike"] - target_strike).abs().idxmin()
    return float(usable.loc[idx, "impliedVolatility"])


def fetch_option_chain_snapshot(symbol: str, spot_price: float, config: dict[str, Any]) -> OptionChainSnapshot:
    """Best-effort fetch + compute of ATM/OTM IVs and skew for one symbol."""
    cfg = config["strategies"]["skew"]

    try:
        expirations = data_collector.list_expirations(symbol)
    except Exception as exc:  # noqa: BLE001
        return OptionChainSnapshot(symbol, None, None, None, None, None, f"No options data: {exc}")

    expiration = _pick_expiration(expirations, cfg["target_expiration_min_days"], cfg["target_expiration_max_days"])
    if expiration is None:
        return OptionChainSnapshot(
            symbol, None, None, None, None, None,
            f"No expiration in the {cfg['target_expiration_min_days']}-{cfg['target_expiration_max_days']} day window",
        )

    try:
        calls, puts = data_collector.fetch_raw_option_chain(symbol, expiration)
    except Exception as exc:  # noqa: BLE001
        return OptionChainSnapshot(symbol, expiration, None, None, None, None, f"Chain fetch failed: {exc}")

    otm_pct = cfg["otm_pct"]
    otm_call_strike = spot_price * (1 + otm_pct)
    otm_put_strike = spot_price * (1 - otm_pct)

    atm_call_iv = _nearest_strike_iv(calls, spot_price)
    atm_put_iv = _nearest_strike_iv(puts, spot_price)
    atm_candidates = [v for v in (atm_call_iv, atm_put_iv) if v is not None]
    atm_iv = sum(atm_candidates) / len(atm_candidates) if atm_candidates else None

    otm_call_iv = _nearest_strike_iv(calls, otm_call_strike)
    otm_put_iv = _nearest_strike_iv(puts, otm_put_strike)

    if atm_iv is None or atm_iv <= 0 or otm_call_iv is None or otm_put_iv is None:
        return OptionChainSnapshot(
            symbol, expiration, atm_iv, otm_call_iv, otm_put_iv, None,
            "Usable implied volatility missing on ATM/OTM strikes for this expiration",
        )

    skew = (otm_put_iv - otm_call_iv) / atm_iv
    return OptionChainSnapshot(symbol, expiration, atm_iv, otm_call_iv, otm_put_iv, skew, None)


def fetch_all_skew_snapshots(
    symbols: list[str], spot_prices: dict[str, float], config: dict[str, Any], logger: logging.Logger
) -> dict[str, OptionChainSnapshot]:
    """Best-effort skew fetch for every symbol. Never raises - failures are logged and skipped."""
    from .utils import safe_run

    results: dict[str, OptionChainSnapshot] = {}
    for symbol in symbols:
        spot = spot_prices.get(symbol)
        if spot is None:
            continue
        snapshot = safe_run(
            logger, f"{symbol} options skew", lambda s=symbol, p=spot: fetch_option_chain_snapshot(s, p, config)
        )
        if snapshot is not None:
            results[symbol] = snapshot
            if snapshot.skew is None:
                logger.info("Skew unavailable for %s: %s", symbol, snapshot.reason)
    return results
