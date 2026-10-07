"""Phase 7 continuation - DRY_RUN order review: converts an approved Top
Candidate into an OrderIntent, validates it (ticker/side/quantity/order
type/limit price/risk/duplicate/market hours), and reports exactly what
WOULD be submitted - without ever calling `broker.submit_order()`.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import order_manager, order_review, order_state
from src.execution.broker import FakeBroker

logger = logging.getLogger("test")

PERMISSIVE_CONFIG = {
    "execution_risk": {"max_risk_per_trade_pct": 0.05},
    "risk": {"account_equity": 10_000},
    "execution": {"trading_hours_start": "00:00", "trading_hours_end": "23:59"},
}

WEEKDAY_NOON_NY = datetime(2026, 9, 9, 12, 0, tzinfo=ZoneInfo("America/New_York"))  # Wednesday


def good_record(**overrides):
    record = {
        "symbol": "AMD", "strategy": "Trend Following", "score": 90,
        "entry": 100.0, "stop_loss": 95.0, "target": 115.0,
        "shares": 10, "dollar_risk": 50.0, "regime_at_entry": "TRENDING_UP",
    }
    record.update(overrides)
    return record


# --- review_order_intent: the happy path ----------------------------------------------


def test_valid_candidate_would_submit_with_no_reasons():
    review = order_review.review_order_intent(good_record(), PERMISSIVE_CONFIG, manager=None, current_market_price=100.0, now=WEEKDAY_NOON_NY)
    assert review.would_submit is True
    assert review.reasons == []
    assert review.intent is not None
    assert review.intent.ticker == "AMD"
    assert review.intent.side == order_state.SIDE_BUY
    assert review.intent.quantity == 10


def test_review_never_touches_any_broker_object():
    """There is no broker parameter on review_order_intent() at all - the
    strongest proof is simply that calling it successfully requires
    nothing broker-shaped. This test documents that contract."""
    import inspect

    params = set(inspect.signature(order_review.review_order_intent).parameters.keys())
    assert "broker" not in params


# --- each validation category can independently block submission -------------------


def test_invalid_intent_blocks_submission_with_validation_reasons():
    record = good_record(stop_loss=105.0, target=90.0)  # inverted for a long
    review = order_review.review_order_intent(record, PERMISSIVE_CONFIG, manager=None, current_market_price=100.0, now=WEEKDAY_NOON_NY)
    assert review.would_submit is False
    assert any("internally consistent" in r for r in review.reasons)


def test_risk_amount_over_limit_blocks_submission():
    record = good_record(dollar_risk=5000.0)
    review = order_review.review_order_intent(record, PERMISSIVE_CONFIG, manager=None, current_market_price=100.0, now=WEEKDAY_NOON_NY)
    assert review.would_submit is False
    assert any("max_risk_per_trade_pct" in r for r in review.reasons)


def test_outside_trading_hours_blocks_submission():
    sunday = datetime(2026, 9, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    review = order_review.review_order_intent(good_record(), PERMISSIVE_CONFIG, manager=None, current_market_price=100.0, now=sunday)
    assert review.would_submit is False
    assert "OUTSIDE_TRADING_HOURS" in review.reasons


def test_price_moved_too_far_blocks_submission():
    review = order_review.review_order_intent(good_record(), PERMISSIVE_CONFIG, manager=None, current_market_price=150.0, now=WEEKDAY_NOON_NY)
    assert review.would_submit is False
    assert "PRICE_MOVED_TOO_FAR" in review.reasons


def test_missing_current_price_blocks_submission_never_reviews_blind():
    review = order_review.review_order_intent(good_record(), PERMISSIVE_CONFIG, manager=None, current_market_price=None, now=WEEKDAY_NOON_NY)
    assert review.would_submit is False
    assert "PRICE_MOVED_TOO_FAR" in review.reasons


def test_duplicate_intent_blocks_submission_when_manager_reports_one():
    broker = FakeBroker()
    broker.connect()
    manager = order_manager.OrderManager(broker, PERMISSIVE_CONFIG)
    existing_intent = order_state.build_order_intent(
        ticker="AMD",
        position={"entry": 100.0, "stop_loss": 95.0, "target": 115.0, "shares": 10, "dollar_risk": 50.0},
        strategy="Trend Following", signal_score=90, quant_score=None, account_mode_at_creation="PAPER",
        trade_id="AMD_2026-09-09_aaaaaaaa",
    )
    manager.submit_entry(existing_intent)

    review = order_review.review_order_intent(good_record(), PERMISSIVE_CONFIG, manager, current_market_price=100.0, now=WEEKDAY_NOON_NY)
    assert review.would_submit is False
    assert "DUPLICATE_INTENT" in review.reasons
    assert len(broker.submitted_intents) == 1  # only the pre-existing one - the review itself submitted nothing


def test_manager_none_skips_duplicate_check_without_crashing():
    review = order_review.review_order_intent(good_record(), PERMISSIVE_CONFIG, manager=None, current_market_price=100.0, now=WEEKDAY_NOON_NY)
    assert "DUPLICATE_INTENT" not in review.reasons


def test_multiple_simultaneous_reasons_are_all_reported_and_deduplicated():
    record = good_record(dollar_risk=5000.0)
    sunday = datetime(2026, 9, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    review = order_review.review_order_intent(record, PERMISSIVE_CONFIG, manager=None, current_market_price=150.0, now=sunday)
    assert review.would_submit is False
    assert len(review.reasons) == len(set(review.reasons))  # no duplicated reason strings
    assert "OUTSIDE_TRADING_HOURS" in review.reasons
    assert "PRICE_MOVED_TOO_FAR" in review.reasons
    assert any("max_risk_per_trade_pct" in r for r in review.reasons)


# --- format_order_review: readable, never fabricates, always says DRY_RUN -----------


def test_format_order_review_includes_every_key_field():
    review = order_review.review_order_intent(good_record(), PERMISSIVE_CONFIG, manager=None, current_market_price=100.0, now=WEEKDAY_NOON_NY)
    text = order_review.format_order_review(review)
    assert "AMD" in text
    assert "Would submit: YES" in text
    assert "Quantity: 10" in text
    assert "no broker was contacted, no order was placed" in text


def test_format_order_review_lists_blocked_reasons_when_present():
    sunday = datetime(2026, 9, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    review = order_review.review_order_intent(good_record(), PERMISSIVE_CONFIG, manager=None, current_market_price=100.0, now=sunday)
    text = order_review.format_order_review(review)
    assert "Would submit: NO" in text
    assert "Blocked reasons:" in text
    assert "OUTSIDE_TRADING_HOURS" in text


def test_format_order_review_handles_missing_intent_gracefully():
    review = order_review.OrderReview(ticker="XYZ", would_submit=False, intent=None, reasons=["could not build"])
    text = order_review.format_order_review(review)
    assert "XYZ" in text
    assert "could not construct" in text


# --- CLI ---------------------------------------------------------------------------


def test_main_cli_reviews_a_manual_order_never_touches_a_broker(monkeypatch, capsys):
    from src import utils

    monkeypatch.setattr(utils, "load_config", lambda path=None: PERMISSIVE_CONFIG)
    monkeypatch.setattr(
        sys, "argv",
        ["order_review", "--ticker", "AMD", "--entry", "100.0", "--stop-loss", "95.0", "--target", "115.0", "--shares", "10", "--dollar-risk", "50.0"],
    )
    exit_code = order_review.main()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "AMD" in out
    assert "no broker was contacted" in out
