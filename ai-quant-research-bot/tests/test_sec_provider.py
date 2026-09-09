"""Tests for src/data_providers/sec_provider.py: 13F manager-level holdings
parsing/change classification and Form 4 insider transaction classification,
with a focus on point-in-time correctness (filing date, not report period or
transaction date, controls `available_at`)."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_providers import base, sec_provider as sp

XML_13F = b"""<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>NVIDIA CORP</nameOfIssuer>
    <cusip>67066G104</cusip>
    <value>500000</value>
    <shrsOrPrnAmt><sshPrnamt>10000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
  </infoTable>
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <cusip>037833100</cusip>
    <value>250000</value>
    <shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
  </infoTable>
</informationTable>
"""


def _make_form4_xml(code: str, shares: str = "1000", price: str = "120.50", txn_date: str = "2026-08-01") -> bytes:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer>
    <issuerName>NVIDIA CORP</issuerName>
    <issuerTradingSymbol>NVDA</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>CFO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>{txn_date}</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
""".encode()


# --- 13F parsing -------------------------------------------------------------------


def test_manager_filing_parses_correctly():
    rows = sp.parse_13f_info_table_xml(XML_13F)
    assert len(rows) == 2
    assert rows[0]["issuer_name"] == "NVIDIA CORP"
    assert rows[0]["cusip"] == "67066G104"
    assert rows[0]["value_thousands"] == "500000"
    assert rows[0]["shares"] == "10000"


def test_build_holdings_resolves_ticker_via_cusip_map_and_computes_weight():
    rows = sp.parse_13f_info_table_xml(XML_13F)
    holdings = sp.build_holdings_from_filing(
        manager_cik="1234567",
        manager_name="Example Capital",
        filing_date="2026-08-14",
        report_period="2026-06-30",
        raw_rows=rows,
        cusip_ticker_map={"67066G104": "NVDA", "037833100": "AAPL"},
    )
    nvda = next(h for h in holdings if h.ticker == "NVDA")
    assert nvda.market_value == 500_000_000.0
    assert nvda.portfolio_weight == 500_000_000.0 / 750_000_000.0
    assert nvda.available_at == date(2026, 8, 14)


def test_unmapped_cusip_yields_no_fabricated_ticker():
    rows = sp.parse_13f_info_table_xml(XML_13F)
    holdings = sp.build_holdings_from_filing(
        manager_cik="1234567", manager_name="Example Capital",
        filing_date="2026-08-14", report_period="2026-06-30",
        raw_rows=rows, cusip_ticker_map={},
    )
    assert all(h.ticker is None for h in holdings)
    assert all(h.ticker_confidence is None for h in holdings)


def _holding(ticker, shares, value, cusip="67066G104", filing_date="2026-08-14", report_period="2026-06-30"):
    return sp.Holding(
        manager_cik="1234567", manager_name="Example Capital", filing_date=date.fromisoformat(filing_date),
        report_period=date.fromisoformat(report_period), issuer_name="NVIDIA CORP", cusip=cusip,
        ticker=ticker, shares=shares, market_value=value, portfolio_weight=1.0, ticker_confidence=1.0,
    )


def test_new_position_calculated_correctly_with_no_prior_filing():
    current = [_holding("NVDA", 1000, 100_000)]
    changes = sp.compute_holding_changes(current, prior=None)
    assert len(changes) == 1
    assert changes[0].classification == sp.CHANGE_NEW
    assert changes[0].prior_shares == 0.0


def test_increased_position_calculated_correctly():
    prior = [_holding("NVDA", 1000, 100_000)]
    current = [_holding("NVDA", 1500, 150_000)]
    changes = sp.compute_holding_changes(current, prior)
    assert changes[0].classification == sp.CHANGE_INCREASED
    assert changes[0].shares_change_pct == 50.0
    assert changes[0].value_change_pct == 50.0


def test_reduced_position_calculated_correctly():
    prior = [_holding("NVDA", 1000, 100_000)]
    current = [_holding("NVDA", 400, 40_000)]
    changes = sp.compute_holding_changes(current, prior)
    assert changes[0].classification == sp.CHANGE_REDUCED
    assert changes[0].shares_change_pct == -60.0


def test_exited_position_calculated_correctly_when_absent_from_current():
    prior = [_holding("NVDA", 1000, 100_000)]
    current: list[sp.Holding] = []
    changes = sp.compute_holding_changes(current, prior)
    assert len(changes) == 1
    assert changes[0].classification == sp.CHANGE_EXITED
    assert changes[0].shares == 0.0
    assert changes[0].shares_change_pct == -100.0


def test_unchanged_position_classified_correctly():
    prior = [_holding("NVDA", 1000, 100_000)]
    current = [_holding("NVDA", 1000, 100_000)]
    changes = sp.compute_holding_changes(current, prior)
    assert changes[0].classification == sp.CHANGE_UNCHANGED


def test_filing_date_controls_available_at_not_report_period():
    holding = _holding("NVDA", 1000, 100_000, filing_date="2026-08-14", report_period="2026-06-30")
    assert holding.available_at == date(2026, 8, 14)
    assert holding.available_at != holding.report_period


def test_report_period_date_cannot_leak_data_early():
    """A June 30 report period filed August 14 must not be treated as known
    on, say, July 1 - available_at must be the later filing date."""
    holding = _holding("NVDA", 1000, 100_000, filing_date="2026-08-14", report_period="2026-06-30")
    july_1 = date(2026, 7, 1)
    assert holding.available_at > july_1  # not knowable that early


def test_institutional_raw_facts_never_labels_bullish_or_bearish():
    prior = [_holding("NVDA", 1000, 100_000)]
    current = [_holding("NVDA", 2000, 200_000)]
    changes = sp.compute_holding_changes(current, prior)
    facts = sp.institutional_raw_facts("NVDA", [changes])
    assert facts["increased_positions"] == 1
    assert "bullish" not in str(facts).lower()
    assert "bearish" not in str(facts).lower()
    assert facts["has_data"] is True


def test_institutional_raw_facts_reports_no_data_for_untouched_ticker():
    facts = sp.institutional_raw_facts("MSFT", [[]])
    assert facts["has_data"] is False


# --- Form 4 parsing/classification --------------------------------------------------


def test_open_market_buy_classified_correctly():
    parsed = sp.parse_form4_xml(_make_form4_xml("P"))
    txns = sp.build_insider_transactions(parsed, filing_date="2026-08-03")
    assert txns[0].classification == "open_market_buy"
    assert txns[0].transaction_value == 1000 * 120.50


def test_open_market_sale_classified_correctly():
    parsed = sp.parse_form4_xml(_make_form4_xml("S"))
    txns = sp.build_insider_transactions(parsed, filing_date="2026-08-03")
    assert txns[0].classification == "open_market_sell"


def test_grant_not_misclassified_as_ordinary_buy():
    parsed = sp.parse_form4_xml(_make_form4_xml("A"))
    txns = sp.build_insider_transactions(parsed, filing_date="2026-08-03")
    assert txns[0].classification == "grant_or_award"
    assert txns[0].classification != "open_market_buy"


def test_option_exercise_not_misclassified_as_ordinary_buy():
    parsed = sp.parse_form4_xml(_make_form4_xml("M"))
    txns = sp.build_insider_transactions(parsed, filing_date="2026-08-03")
    assert txns[0].classification == "option_exercise"


def test_gift_not_misclassified_as_ordinary_sale():
    parsed = sp.parse_form4_xml(_make_form4_xml("G"))
    txns = sp.build_insider_transactions(parsed, filing_date="2026-08-03")
    assert txns[0].classification == "gift"


def test_grants_and_exercises_excluded_from_insider_buy_sell_features():
    parsed = sp.parse_form4_xml(_make_form4_xml("A"))
    txns = sp.build_insider_transactions(parsed, filing_date="2026-08-03")
    features = sp.compute_insider_features(txns, as_of=date(2026, 8, 20))
    assert features["insider_buy_value_30d"] == 0
    assert features["insider_buy_count_30d"] == 0


def test_filing_date_availability_is_respected_for_insider_features():
    """A transaction filed AFTER `as_of` must not count, even if the trade
    itself happened before `as_of`."""
    parsed = sp.parse_form4_xml(_make_form4_xml("P", txn_date="2026-08-01"))
    txns = sp.build_insider_transactions(parsed, filing_date="2026-08-25")
    features_before_filing = sp.compute_insider_features(txns, as_of=date(2026, 8, 10))
    features_after_filing = sp.compute_insider_features(txns, as_of=date(2026, 8, 26))
    assert features_before_filing["insider_buy_count_30d"] == 0
    assert features_after_filing["insider_buy_count_30d"] == 1


def test_cluster_buying_flag_set_with_multiple_distinct_insiders():
    txns = []
    for i in range(3):
        parsed = sp.parse_form4_xml(_make_form4_xml("P", txn_date="2026-08-01"))
        built = sp.build_insider_transactions(parsed, filing_date="2026-08-03")
        # give each transaction a distinct insider name
        txns.append(built[0].__class__(**{**built[0].__dict__, "insider_name": f"Insider {i}"}))
    features = sp.compute_insider_features(txns, as_of=date(2026, 8, 20))
    assert features["cluster_buying"] is True


def test_cluster_buying_flag_not_set_for_a_single_insider_buying_repeatedly():
    txns = []
    for _ in range(3):
        parsed = sp.parse_form4_xml(_make_form4_xml("P", txn_date="2026-08-01"))
        txns.extend(sp.build_insider_transactions(parsed, filing_date="2026-08-03"))
    features = sp.compute_insider_features(txns, as_of=date(2026, 8, 20))
    # 3 buys but only 1 distinct insider -> below the distinct-insider threshold
    assert features["cluster_buying"] is False


def test_net_insider_value_combines_buys_and_sells():
    buy_parsed = sp.parse_form4_xml(_make_form4_xml("P", shares="1000", price="100", txn_date="2026-08-01"))
    sell_parsed = sp.parse_form4_xml(_make_form4_xml("S", shares="500", price="100", txn_date="2026-08-02"))
    txns = sp.build_insider_transactions(buy_parsed, filing_date="2026-08-03") + sp.build_insider_transactions(
        sell_parsed, filing_date="2026-08-03"
    )
    features = sp.compute_insider_features(txns, as_of=date(2026, 8, 20))
    assert features["insider_buy_value_30d"] == 100_000
    assert features["insider_sell_value_30d"] == 50_000
    assert features["net_insider_value_30d"] == 50_000


# --- live-fetch gating (network never required, never fabricated) ------------------


def test_fetch_recent_filings_unavailable_without_sec_identity(monkeypatch):
    monkeypatch.delenv("SEC_IDENTITY", raising=False)
    result = sp.fetch_recent_filings("0001234567")
    assert result.status == base.STATUS_UNAVAILABLE
    assert result.data is None


def test_sec_identity_configured_reflects_env(monkeypatch):
    monkeypatch.delenv("SEC_IDENTITY", raising=False)
    assert sp.sec_identity_configured() is False
    monkeypatch.setenv("SEC_IDENTITY", "Test Co test@example.com")
    assert sp.sec_identity_configured() is True
