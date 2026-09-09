"""Macro context provider - FRED (Federal Reserve Economic Data).

Gated entirely on `FRED_API_KEY` (see .env.example). With no key configured,
every function here returns `base.unavailable()` cleanly - it never raises,
never crashes the daily run, and never invents a value. The same applies to
any network/parse failure once a key IS configured: `base.provider_error()`,
not an exception, not a guessed number.

`available_at` for a macro series point should be its ACTUAL FRED release
date when known (FRED's own `realtime_start` field, when the API returns it),
not the period the observation describes - the same point-in-time principle
used for SEC filings. CPI for "March" is not knowable in March; it's released
in April. Where a release date can't be determined, this module never
backdates a value to look known earlier than it safely can be treated as
known - see `fetch_series_latest()`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from . import base

SOURCE_FRED = "fred"

FRED_SERIES_URL = "https://api.stlouisfed.org/fred/series/observations"

# Series ids worth wiring up now - fetch_series_latest() works with any FRED
# series id, this is just a documented, human-readable subset.
SERIES_FED_FUNDS_RATE = "FEDFUNDS"
SERIES_10Y_TREASURY = "DGS10"
SERIES_2Y_TREASURY = "DGS2"
SERIES_10Y_2Y_SPREAD = "T10Y2Y"
SERIES_CPI = "CPIAUCSL"
SERIES_UNEMPLOYMENT = "UNRATE"


def fred_configured() -> bool:
    return bool(os.environ.get("FRED_API_KEY"))


@dataclass(frozen=True)
class MacroObservation:
    series_id: str
    value: float
    observation_date: date
    release_date: date | None  # when FRED actually published this point, if known

    @property
    def available_at(self) -> date:
        """Point-in-time discipline: prefer the actual release date; fall back
        to the observation date only when FRED doesn't report one - never
        earlier than either, never fabricated."""
        return self.release_date or self.observation_date


def _parse_fred_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_fred_observations(payload: dict[str, Any], series_id: str) -> list[MacroObservation]:
    """Parse a FRED `series/observations` JSON payload (or an equivalently-
    shaped test fixture) into normalized, point-in-time-aware observations.
    Skips any observation whose value is FRED's own missing-data sentinel
    (".") rather than fabricating a numeric value for it."""
    observations = []
    for row in payload.get("observations", []):
        raw_value = row.get("value")
        if raw_value in (None, ".", ""):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        obs_date = _parse_fred_date(row.get("date"))
        if obs_date is None:
            continue
        observations.append(
            MacroObservation(
                series_id=series_id,
                value=value,
                observation_date=obs_date,
                release_date=_parse_fred_date(row.get("realtime_start")),
            )
        )
    return observations


def compute_yield_curve_spread(ten_year: MacroObservation | None, two_year: MacroObservation | None) -> float | None:
    """10Y-2Y spread - never fabricated if either leg is missing."""
    if ten_year is None or two_year is None:
        return None
    return round(ten_year.value - two_year.value, 4)


def fetch_series_latest(series_id: str, api_key: str | None = None, timeout: int = 10, limit: int = 1) -> base.ProviderResult[list[MacroObservation]]:
    """Fetch the most recent observation(s) for a FRED series. Returns
    `unavailable()` with no key configured; `provider_error()` on any
    network/parse failure. Never raises out of this function."""
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        return base.unavailable(SOURCE_FRED, "FRED_API_KEY not configured (see .env.example)")

    import requests

    try:
        resp = requests.get(
            FRED_SERIES_URL,
            params={
                "series_id": series_id,
                "api_key": key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - network/parse isolation boundary
        return base.provider_error(SOURCE_FRED, f"failed to fetch FRED series {series_id}: {exc}")

    observations = parse_fred_observations(payload, series_id)
    if not observations:
        return base.unavailable(SOURCE_FRED, f"FRED returned no usable observations for {series_id}")

    latest = observations[0]
    return base.ok(
        SOURCE_FRED,
        observations,
        available_at=latest.available_at,
        freshness="point_in_time" if latest.release_date else "observation_date_only",
    )
