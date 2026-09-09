"""SEC EDGAR provider: institutional 13F holdings + Form 4 insider transactions.

Important correction carried through this whole module: a 13F is filed by an
institutional investment MANAGER (e.g. a hedge fund or asset manager), not by
the company whose stock it holds. Every 13F-derived object here is keyed by
manager first (`manager_cik`/`manager_name`), with the held ticker/CUSIP as a
field on that record - never the other way around.

Point-in-time discipline (see README "Point-in-time correctness"): a 13F's
`report_period` is the quarter-end the snapshot describes, but that is NOT
when the information became public. A manager can legally wait up to 45 days
after quarter-end to file, so a June 30 position might not be filed until
August 14. Every normalized object's `available_at` is therefore always the
FILING date, never the report period - `report_period` is kept only as
descriptive metadata about which quarter the snapshot covers. The same rule
applies to Form 4: `available_at` is the filing date, not the transaction
date (which can itself lag a few business days behind the trade).

Live fetching is best-effort and fully optional: every network call is gated
on `SEC_IDENTITY` being set (SEC requires a descriptive User-Agent identifying
the requester - see .env.example) and wrapped so a network failure, timeout,
or missing identity returns `base.unavailable()`/`base.provider_error()`
rather than raising or fabricating data. Nothing in this module - or anywhere
in this codebase - places an order; this only ever reads public filings.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from . import base

SOURCE_13F = "sec_13f"
SOURCE_FORM4 = "sec_form4"

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/index.json"
SEC_ARCHIVES_FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{filename}"

_13F_INFOTABLE_NS = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"

# Form 4 transaction codes that represent a genuine open-market trade. Every
# other code (grants, option exercises, gifts, tax withholding, conversions,
# etc.) is parsed and kept, but never counted as an ordinary buy/sell in the
# insider_*_value_30d features - see classify_transaction().
OPEN_MARKET_BUY_CODE = "P"
OPEN_MARKET_SELL_CODE = "S"

# Every other standard Form 4 code, classified for completeness/reporting -
# none of these count toward the open-market buy/sell features.
_NON_MARKET_CODES = {
    "A": "grant_or_award",
    "M": "option_exercise",
    "C": "conversion",
    "F": "tax_withholding",
    "G": "gift",
    "D": "disposition_to_issuer",
    "X": "option_exercise_in_the_money",
    "J": "other",
    "K": "equity_swap",
    "U": "tender_offer",
}


def sec_identity_configured() -> bool:
    return bool(os.environ.get("SEC_IDENTITY"))


def _identity_headers() -> dict[str, str] | None:
    identity = os.environ.get("SEC_IDENTITY")
    if not identity:
        return None
    return {"User-Agent": identity, "Accept-Encoding": "gzip, deflate"}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# --- 13F: normalized holding + change objects --------------------------------------


@dataclass(frozen=True)
class Holding:
    manager_cik: str
    manager_name: str
    filing_date: date
    report_period: date
    issuer_name: str
    cusip: str
    ticker: str | None
    shares: float
    market_value: float
    portfolio_weight: float | None
    ticker_confidence: float | None

    @property
    def available_at(self) -> date:
        """Point-in-time discipline: what's knowable is gated on FILING date,
        never report_period - see module docstring."""
        return self.filing_date


CHANGE_NEW = "new"
CHANGE_INCREASED = "increased"
CHANGE_REDUCED = "reduced"
CHANGE_EXITED = "exited"
CHANGE_UNCHANGED = "unchanged"


@dataclass(frozen=True)
class HoldingChange:
    manager_cik: str
    manager_name: str
    filing_date: date
    report_period: date
    ticker: str | None
    cusip: str
    shares: float
    prior_shares: float
    shares_change_pct: float | None
    market_value: float
    prior_market_value: float
    value_change_pct: float | None
    classification: str

    @property
    def available_at(self) -> date:
        return self.filing_date


def parse_13f_info_table_xml(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parse a 13F-HR "information table" XML document into raw row dicts.

    Real SEC 13F info tables use the namespace in `_13F_INFOTABLE_NS`; this
    also tolerates an unnamespaced document (some historical filings omit it)
    by falling back to bare tag names.
    """
    root = ET.fromstring(xml_bytes)

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    rows: list[dict[str, Any]] = []
    for info_table in root.iter():
        if _local(info_table.tag) != "infoTable":
            continue

        def _text(parent: ET.Element, name: str) -> str | None:
            for child in parent.iter():
                if _local(child.tag) == name and child is not parent:
                    return (child.text or "").strip() or None
            return None

        shares_amt = None
        share_type = None
        for shrs in info_table.iter():
            if _local(shrs.tag) == "shrsOrPrnAmt":
                shares_amt = _text(shrs, "sshPrnamt")
                share_type = _text(shrs, "sshPrnamtType")
                break

        rows.append(
            {
                "issuer_name": _text(info_table, "nameOfIssuer"),
                "cusip": _text(info_table, "cusip"),
                # SEC 13F "value" is reported in thousands of dollars.
                "value_thousands": _text(info_table, "value"),
                "shares": shares_amt,
                "share_type": share_type,
                "investment_discretion": _text(info_table, "investmentDiscretion"),
            }
        )
    return rows


def build_holdings_from_filing(
    manager_cik: str,
    manager_name: str,
    filing_date: date | str,
    report_period: date | str,
    raw_rows: list[dict[str, Any]],
    cusip_ticker_map: dict[str, str] | None = None,
) -> list[Holding]:
    """Build normalized `Holding` objects from `parse_13f_info_table_xml()`
    output (or an equivalently-shaped synthetic/test fixture).

    `cusip_ticker_map` is optional and caller-supplied (SEC filings do not
    carry a ticker, only CUSIP) - a CUSIP missing from the map yields
    `ticker=None`, never a guessed symbol. `portfolio_weight` is this
    holding's share of the manager's OWN total 13F value for this filing, not
    of any index or the market."""
    if isinstance(filing_date, str):
        filing_date = _parse_date(filing_date)
    if isinstance(report_period, str):
        report_period = _parse_date(report_period)
    cusip_ticker_map = cusip_ticker_map or {}

    parsed_values = []
    for row in raw_rows:
        try:
            value = float(row.get("value_thousands") or 0) * 1000.0
        except (TypeError, ValueError):
            value = 0.0
        parsed_values.append(value)
    total_value = sum(parsed_values)

    holdings = []
    for row, value in zip(raw_rows, parsed_values):
        cusip = (row.get("cusip") or "").strip()
        try:
            shares = float(row.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        ticker = cusip_ticker_map.get(cusip)
        holdings.append(
            Holding(
                manager_cik=str(manager_cik),
                manager_name=manager_name,
                filing_date=filing_date,
                report_period=report_period,
                issuer_name=row.get("issuer_name") or "",
                cusip=cusip,
                ticker=ticker,
                shares=shares,
                market_value=value,
                portfolio_weight=(value / total_value) if total_value > 0 else None,
                ticker_confidence=1.0 if ticker else None,
            )
        )
    return holdings


def _pct_change(current: float, prior: float) -> float | None:
    if prior == 0:
        return None
    return (current - prior) / prior * 100.0


def compute_holding_changes(
    current: list[Holding], prior: list[Holding] | None
) -> list[HoldingChange]:
    """Compare one manager's current-quarter holdings to their prior-quarter
    holdings (same manager - never mix managers), by CUSIP. `prior=None` or an
    empty list means "no prior filing on record" - every current holding is
    classified `new` in that case (we genuinely don't know it isn't; that is
    the honest classification, not `unchanged`)."""
    prior = prior or []
    prior_by_cusip = {h.cusip: h for h in prior}
    current_cusips = {h.cusip for h in current}

    changes: list[HoldingChange] = []
    for holding in current:
        prior_holding = prior_by_cusip.get(holding.cusip)
        prior_shares = prior_holding.shares if prior_holding else 0.0
        prior_value = prior_holding.market_value if prior_holding else 0.0

        if prior_holding is None:
            classification = CHANGE_NEW
        elif holding.shares > prior_shares:
            classification = CHANGE_INCREASED
        elif holding.shares < prior_shares:
            classification = CHANGE_REDUCED
        else:
            classification = CHANGE_UNCHANGED

        changes.append(
            HoldingChange(
                manager_cik=holding.manager_cik,
                manager_name=holding.manager_name,
                filing_date=holding.filing_date,
                report_period=holding.report_period,
                ticker=holding.ticker,
                cusip=holding.cusip,
                shares=holding.shares,
                prior_shares=prior_shares,
                shares_change_pct=_pct_change(holding.shares, prior_shares),
                market_value=holding.market_value,
                prior_market_value=prior_value,
                value_change_pct=_pct_change(holding.market_value, prior_value),
                classification=classification,
            )
        )

    # Anything the manager held last quarter but no longer appears at all this
    # quarter has been fully exited.
    for cusip, prior_holding in prior_by_cusip.items():
        if cusip in current_cusips:
            continue
        changes.append(
            HoldingChange(
                manager_cik=prior_holding.manager_cik,
                manager_name=prior_holding.manager_name,
                filing_date=max((h.filing_date for h in current), default=prior_holding.filing_date),
                report_period=max((h.report_period for h in current), default=prior_holding.report_period),
                ticker=prior_holding.ticker,
                cusip=cusip,
                shares=0.0,
                prior_shares=prior_holding.shares,
                shares_change_pct=-100.0 if prior_holding.shares else None,
                market_value=0.0,
                prior_market_value=prior_holding.market_value,
                value_change_pct=-100.0 if prior_holding.market_value else None,
                classification=CHANGE_EXITED,
            )
        )
    return changes


def institutional_raw_facts(ticker: str, changes_by_manager: list[list[HoldingChange]]) -> dict[str, Any]:
    """Raw, unopinionated aggregate facts for one ticker across every manager's
    latest classified changes. Deliberately does NOT decide "institutional
    buying = bullish" - it's a plain tally of what filings actually show, left
    for `big_money.py` (or a human) to interpret. `changes_by_manager` is one
    list of `HoldingChange` per manager (their most recent quarter-over-quarter
    comparison); this filters each to the given ticker/cusip before tallying."""
    relevant = [c for manager_changes in changes_by_manager for c in manager_changes if c.ticker == ticker]

    counts = {CHANGE_NEW: 0, CHANGE_INCREASED: 0, CHANGE_REDUCED: 0, CHANGE_EXITED: 0, CHANGE_UNCHANGED: 0}
    total_value_change = 0.0
    managers_reporting = set()
    for change in relevant:
        counts[change.classification] += 1
        managers_reporting.add(change.manager_cik)
        total_value_change += change.market_value - change.prior_market_value

    return {
        "ticker": ticker,
        "managers_reporting": len(managers_reporting),
        "new_positions": counts[CHANGE_NEW],
        "increased_positions": counts[CHANGE_INCREASED],
        "reduced_positions": counts[CHANGE_REDUCED],
        "exited_positions": counts[CHANGE_EXITED],
        "unchanged_positions": counts[CHANGE_UNCHANGED],
        "total_value_change_dollars": round(total_value_change, 2),
        "has_data": len(relevant) > 0,
    }


# --- Form 4: normalized insider transaction objects --------------------------------


@dataclass(frozen=True)
class InsiderTransaction:
    issuer_name: str
    ticker: str | None
    insider_name: str
    insider_title: str | None
    is_director: bool
    is_officer: bool
    transaction_date: date
    filing_date: date
    transaction_code: str
    acquired_disposed: str  # "A" (acquired) | "D" (disposed)
    shares: float
    price_per_share: float | None
    ownership_type: str | None  # "D" direct | "I" indirect
    source_filing_id: str | None

    @property
    def available_at(self) -> date:
        """Point-in-time discipline: gated on FILING date, never transaction
        date - a Form 4 can be filed up to 2 business days after the trade."""
        return self.filing_date

    @property
    def transaction_value(self) -> float | None:
        if self.price_per_share is None:
            return None
        return round(self.shares * self.price_per_share, 2)

    @property
    def is_open_market(self) -> bool:
        return self.transaction_code in (OPEN_MARKET_BUY_CODE, OPEN_MARKET_SELL_CODE)

    @property
    def classification(self) -> str:
        """Distinguishes an ordinary open-market buy/sell from a grant/award,
        option exercise, gift, tax withholding, etc. - see module docstring.
        Never treats all Form 4 activity as equivalent."""
        if self.transaction_code == OPEN_MARKET_BUY_CODE:
            return "open_market_buy"
        if self.transaction_code == OPEN_MARKET_SELL_CODE:
            return "open_market_sell"
        return _NON_MARKET_CODES.get(self.transaction_code, "other_non_market")


def parse_form4_xml(xml_bytes: bytes) -> dict[str, Any]:
    """Parse a Form 4 `ownershipDocument` XML into a raw dict: issuer info,
    reporting owner info, and a list of non-derivative transaction rows. Form
    4 XML is unnamespaced (unlike the 13F info table)."""
    root = ET.fromstring(xml_bytes)

    def _find_text(parent: ET.Element, path: str) -> str | None:
        node = parent.find(path)
        if node is None:
            return None
        text = (node.text or "").strip()
        return text or None

    issuer_name = _find_text(root, "issuer/issuerName")
    ticker = _find_text(root, "issuer/issuerTradingSymbol")

    owner_node = root.find("reportingOwner")
    insider_name = None
    is_director = False
    is_officer = False
    officer_title = None
    if owner_node is not None:
        insider_name = _find_text(owner_node, "reportingOwnerId/rptOwnerName")
        is_director = (_find_text(owner_node, "reportingOwnerRelationship/isDirector") or "0") in ("1", "true")
        is_officer = (_find_text(owner_node, "reportingOwnerRelationship/isOfficer") or "0") in ("1", "true")
        officer_title = _find_text(owner_node, "reportingOwnerRelationship/officerTitle")

    transactions = []
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        transactions.append(
            {
                "transaction_date": _find_text(txn, "transactionDate/value"),
                "transaction_code": _find_text(txn, "transactionCoding/transactionCode"),
                "shares": _find_text(txn, "transactionAmounts/transactionShares/value"),
                "price_per_share": _find_text(txn, "transactionAmounts/transactionPricePerShare/value"),
                "acquired_disposed": _find_text(txn, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                "ownership_type": _find_text(txn, "ownershipNature/directOrIndirectOwnership/value"),
            }
        )

    return {
        "issuer_name": issuer_name,
        "ticker": ticker,
        "insider_name": insider_name,
        "is_director": is_director,
        "is_officer": is_officer,
        "officer_title": officer_title,
        "transactions": transactions,
    }


def build_insider_transactions(
    parsed_filing: dict[str, Any], filing_date: date | str, source_filing_id: str | None = None
) -> list[InsiderTransaction]:
    """Build normalized `InsiderTransaction` objects from
    `parse_form4_xml()` output (or an equivalently-shaped test fixture) plus
    the filing's own filing_date (which the raw document usually does not
    carry itself - it comes from the EDGAR filing index)."""
    if isinstance(filing_date, str):
        filing_date = _parse_date(filing_date)

    results = []
    for row in parsed_filing.get("transactions", []):
        txn_date = _parse_date(row.get("transaction_date"))
        if txn_date is None:
            continue
        try:
            shares = float(row.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        price = row.get("price_per_share")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None

        results.append(
            InsiderTransaction(
                issuer_name=parsed_filing.get("issuer_name") or "",
                ticker=parsed_filing.get("ticker"),
                insider_name=parsed_filing.get("insider_name") or "",
                insider_title=parsed_filing.get("officer_title"),
                is_director=bool(parsed_filing.get("is_director")),
                is_officer=bool(parsed_filing.get("is_officer")),
                transaction_date=txn_date,
                filing_date=filing_date,
                transaction_code=(row.get("transaction_code") or "").strip(),
                acquired_disposed=(row.get("acquired_disposed") or "").strip(),
                shares=shares,
                price_per_share=price,
                ownership_type=row.get("ownership_type"),
                source_filing_id=source_filing_id,
            )
        )
    return results


def compute_insider_features(
    transactions: list[InsiderTransaction],
    as_of: date,
    window_days: int = 30,
    cluster_buy_count_threshold: int = 3,
    cluster_distinct_insiders_threshold: int = 2,
) -> dict[str, Any]:
    """Normalized insider features over a trailing window, counting only
    transactions whose `available_at` (filing date) already falls on or
    before `as_of` AND within the window - never a transaction that hasn't
    been filed yet as of `as_of`, and never one already outside the lookback.
    Only `open_market_buy`/`open_market_sell` count toward buy/sell value and
    counts; grants, option exercises, gifts, etc. never do (see
    `InsiderTransaction.classification`)."""
    window_start = as_of.toordinal() - window_days

    in_window = [
        t for t in transactions if window_start <= t.available_at.toordinal() <= as_of.toordinal()
    ]

    buys = [t for t in in_window if t.classification == "open_market_buy"]
    sells = [t for t in in_window if t.classification == "open_market_sell"]

    buy_value = sum(t.transaction_value or 0.0 for t in buys)
    sell_value = sum(t.transaction_value or 0.0 for t in sells)

    distinct_buying_insiders = {t.insider_name for t in buys}
    cluster_buying = (
        len(buys) >= cluster_buy_count_threshold
        and len(distinct_buying_insiders) >= cluster_distinct_insiders_threshold
    )

    return {
        "as_of": as_of,
        "window_days": window_days,
        "insider_buy_value_30d": round(buy_value, 2),
        "insider_sell_value_30d": round(sell_value, 2),
        "net_insider_value_30d": round(buy_value - sell_value, 2),
        "insider_buy_count_30d": len(buys),
        "insider_sell_count_30d": len(sells),
        "distinct_buying_insiders_30d": len(distinct_buying_insiders),
        "cluster_buying": cluster_buying,
        "has_data": len(in_window) > 0,
    }


# --- best-effort live fetch (network-gated, never required) ------------------------


def fetch_recent_filings(
    cik: str, form_types: tuple[str, ...] = ("13F-HR", "4"), timeout: int = 10
) -> base.ProviderResult[list[dict[str, Any]]]:
    """List a manager/issuer's recent filings of the given form types via
    SEC EDGAR's `submissions` JSON endpoint. Returns `unavailable()` if
    `SEC_IDENTITY` isn't set, `provider_error()` on any network/parse failure -
    never raises, never fabricates a filing."""
    headers = _identity_headers()
    if headers is None:
        return base.unavailable(SOURCE_13F, "SEC_IDENTITY not configured (see .env.example)")

    import requests

    try:
        cik_padded = str(cik).zfill(10)
        resp = requests.get(SEC_SUBMISSIONS_URL.format(cik=cik_padded), headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - network/parse isolation boundary
        return base.provider_error(SOURCE_13F, f"failed to fetch SEC submissions for CIK {cik}: {exc}")

    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])

    filings = [
        {
            "form": form,
            "filing_date": filing_dates[i] if i < len(filing_dates) else None,
            "accession_number": accession_numbers[i] if i < len(accession_numbers) else None,
            "primary_document": primary_documents[i] if i < len(primary_documents) else None,
        }
        for i, form in enumerate(forms)
        if form in form_types
    ]
    return base.ok(SOURCE_13F, filings, available_at=base.utcnow(), freshness="live")


def fetch_filing_document(cik: str, accession_number: str, filename: str, timeout: int = 10) -> base.ProviderResult[bytes]:
    """Fetch one raw document (e.g. a 13F info table or Form 4 XML) from a
    filing's own archive directory. Same SEC_IDENTITY gating as
    `fetch_recent_filings`."""
    headers = _identity_headers()
    if headers is None:
        return base.unavailable(SOURCE_13F, "SEC_IDENTITY not configured (see .env.example)")

    import requests

    try:
        accession_nodash = accession_number.replace("-", "")
        url = SEC_ARCHIVES_FILE_URL.format(cik=str(cik).lstrip("0") or "0", accession_nodash=accession_nodash, filename=filename)
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return base.provider_error(SOURCE_13F, f"failed to fetch filing document {filename}: {exc}")

    return base.ok(SOURCE_13F, resp.content, available_at=base.utcnow(), freshness="live")
