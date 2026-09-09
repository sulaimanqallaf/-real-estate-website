"""Shared provider contract: `ProviderResult` and the status constants every
provider in this package returns, so callers never have to guess a provider's
shape or silently treat "unavailable" as "zero"/"neutral".

Every provider result carries:

- `source`: which provider/data source produced this (e.g. "sec_13f", "fred",
  "options_flow_mock").
- `status`: one of `STATUS_OK` / `STATUS_UNAVAILABLE` / `STATUS_ERROR`. Only
  `STATUS_OK` means `data` is meaningful - `STATUS_UNAVAILABLE` (not
  configured, or the underlying source genuinely has nothing) and
  `STATUS_ERROR` (a fetch/parse failure) both mean "don't trust `data`," and
  are kept distinct so a caller/report can say *why* something is missing
  instead of flattening every non-answer into the same blank.
- `data`: the payload, or `None` unless `status == STATUS_OK`.
- `fetched_at`: when THIS process fetched/computed the result (wall-clock,
  informational).
- `available_at`: when the underlying fact would have actually been knowable
  in the real world - e.g. a 13F's SEC *filing* date, not its report-period
  end; a Form 4's filing date; the bar index at which a swing high has enough
  future bars to be confirmed. This is the field point-in-time correctness is
  built on - see README "Point-in-time correctness".
- `freshness`: a short label for how current/lagged the data is (e.g.
  "point_in_time", "delayed_15m", "quarterly_lag") - informational, never used
  to change a value, only to describe one.
- `error`: a short human-readable reason when `status != STATUS_OK`.
- `confidence`: 0.0-1.0 when a provider has a meaningful basis for one (e.g.
  ticker-from-CUSIP resolution confidence); `None` when there's no honest
  basis for a number - never a fabricated placeholder.

Never fabricate a missing value: a provider that can't answer returns
`unavailable()` or `error()`, not a guessed 0/neutral/default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generic, Protocol, TypeVar

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"

T = TypeVar("T")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    source: str
    status: str
    data: T | None = None
    fetched_at: datetime | None = None
    available_at: datetime | None = None
    freshness: str | None = None
    error: str | None = None
    confidence: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK and self.data is not None


def ok(
    source: str,
    data: T,
    available_at: datetime | None = None,
    freshness: str | None = None,
    confidence: float | None = None,
) -> ProviderResult[T]:
    return ProviderResult(
        source=source,
        status=STATUS_OK,
        data=data,
        fetched_at=utcnow(),
        available_at=available_at,
        freshness=freshness,
        confidence=confidence,
    )


def unavailable(source: str, error: str | None = None) -> ProviderResult[Any]:
    """The source has nothing to say (not configured, no matching data) - not a
    failure, just an honest absence."""
    return ProviderResult(source=source, status=STATUS_UNAVAILABLE, fetched_at=utcnow(), error=error)


def provider_error(source: str, error: str) -> ProviderResult[Any]:
    """A fetch/parse actually failed (network, malformed response). Distinct
    from `unavailable` so a caller/report can tell "nothing there" apart from
    "something broke.\""""
    return ProviderResult(source=source, status=STATUS_ERROR, fetched_at=utcnow(), error=error)


class DataProvider(Protocol):
    """Minimal shape every provider in this package satisfies. Not every
    provider needs a single "fetch" signature (13F/Form4 take a manager or
    ticker; macro takes a series id; options flow takes a ticker) - this
    Protocol exists for the parts that ARE common: a name, and whether the
    provider is configured to do anything beyond return `unavailable`."""

    name: str

    def is_configured(self) -> bool: ...
