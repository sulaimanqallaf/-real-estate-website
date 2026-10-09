"""Property-based tests for this project's safety-critical invariants
(Sprint 3, Reliability milestone: "use Hypothesis... where
beneficial").

**Why Hypothesis here specifically, not just more hand-picked
examples**: `order_state.validate_intent()`, `risk_manager._size_
position()`, and `circuit_breaker`'s threshold checks are exactly the
kind of code where a hand-picked example test proves "this one input
is handled correctly" but says nothing about the boundary cases a
real run could hit (a NaN price, a huge quantity, an exact-equal-to-
the-limit value, a negative config value nobody anticipated). These
are the functions standing between a decision and a real broker order
- Hypothesis generates hundreds of adversarial inputs per run
specifically to find the boundary case a hand-picked example would
never think to try, and the properties below are each a safety
invariant that must hold for EVERY input, not just the examples this
project's other tests already cover (which stay in place - this file
doesn't replace them).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from src.execution import circuit_breaker, order_state
from src import risk_manager

# Keep examples small in count (this suite runs as part of the full
# regression, not a standalone fuzzing campaign) - still far more
# combinations than any hand-written example list.
_FAST = settings(max_examples=50, deadline=None)


# --- order_state.validate_intent(): never crashes, never accepts an invalid intent ------


def _make_intent(**overrides) -> order_state.OrderIntent:
    base = dict(
        intent_id="intent_test", ticker="AAPL", side=order_state.SIDE_BUY, quantity=10,
        order_type=order_state.ORDER_TYPE_LIMIT, entry_price=100.0, stop_loss=95.0, target_price=110.0,
        strategy="Trend Following", signal_score=5, quant_score=0.5, risk_amount=50.0,
        created_at=datetime.now(timezone.utc), account_mode_at_creation="PAPER",
    )
    base.update(overrides)
    return order_state.OrderIntent(**base)


_SAFE_CONFIG = {"execution_risk": {"max_risk_per_trade_pct": 0.01}, "risk": {"account_equity": 10000.0}}


@given(side=st.text(min_size=0, max_size=10).filter(lambda s: s != order_state.SIDE_BUY))
@_FAST
def test_validate_intent_rejects_every_non_buy_side(side):
    """Long-only is a hard safety invariant (Part E) - no randomly
    generated string other than the exact literal 'BUY' may ever pass."""
    errors = order_state.validate_intent(_make_intent(side=side), _SAFE_CONFIG)
    assert errors, f"side={side!r} was wrongly accepted"


@given(quantity=st.integers(max_value=0))
@_FAST
def test_validate_intent_rejects_every_non_positive_quantity(quantity):
    errors = order_state.validate_intent(_make_intent(quantity=quantity), _SAFE_CONFIG)
    assert errors


@given(
    entry=st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False),
    stop=st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False),
    target=st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False),
)
@_FAST
def test_validate_intent_enforces_long_geometry_for_any_price_combination(entry, stop, target):
    """stop < entry < target is required for ANY long position - this
    must hold (or be rejected) for every price combination Hypothesis
    throws at it, not just the hand-picked 95/100/110 example."""
    intent = _make_intent(entry_price=entry, stop_loss=stop, target_price=target)
    errors = order_state.validate_intent(intent, _SAFE_CONFIG)
    geometry_ok = stop < entry < target
    if not geometry_ok:
        assert errors, f"invalid geometry (stop={stop}, entry={entry}, target={target}) was wrongly accepted"


_ANY_PRICE = st.floats(min_value=-1e12, max_value=1e12, allow_nan=False, allow_infinity=False) | st.just(float("nan")) | st.just(float("inf")) | st.just(float("-inf"))


@given(
    entry=_ANY_PRICE,
    stop=_ANY_PRICE,
    target=_ANY_PRICE,
    quantity=st.integers(min_value=-1_000_000, max_value=1_000_000),
    side=st.text(max_size=10),
)
@_FAST
def test_validate_intent_never_raises_on_any_input(entry, stop, target, quantity, side):
    """Fail CLOSED means reject-with-a-reason, never crash. Even NaN/
    inf prices or garbage strings must come back as a (possibly
    non-empty) list, never an exception - a crash here would be worse
    than a rejection, since order_manager.py's caller wouldn't get a
    clean 'refuse to submit' signal at all."""
    intent = _make_intent(entry_price=entry, stop_loss=stop, target_price=target, quantity=quantity, side=side)
    errors = order_state.validate_intent(intent, _SAFE_CONFIG)
    assert isinstance(errors, list)


@given(risk_amount=st.floats(min_value=1_000.01, max_value=1_000_000.0, allow_nan=False))
@_FAST
def test_validate_intent_rejects_any_risk_amount_over_the_configured_limit(risk_amount):
    """max_risk_per_trade_pct=0.01 * account_equity=10000 -> limit is
    exactly $100 - any amount strictly over that (with margin for the
    function's own 1e-6 tolerance) must always be rejected."""
    errors = order_state.validate_intent(_make_intent(risk_amount=risk_amount), _SAFE_CONFIG)
    assert errors


# --- risk_manager._size_position(): never risks more than the configured budget ---------


@given(
    entry=st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
    risk_per_share=st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False),
    account_equity=st.floats(min_value=100.0, max_value=10_000_000.0, allow_nan=False, allow_infinity=False),
    risk_pct=st.floats(min_value=0.01, max_value=10.0, allow_nan=False, allow_infinity=False),
)
@_FAST
def test_position_sizing_never_exceeds_the_dollar_risk_budget(entry, risk_per_share, account_equity, risk_pct):
    """The core sizing safety invariant: for ANY valid entry/stop/
    account/risk-pct combination, the resulting dollar_risk must never
    exceed the configured budget (account_equity * risk_pct%) - this
    is what keeps a single trade from ever risking more than intended,
    regardless of how oddly entry/stop/config values combine."""
    stop = entry - risk_per_share
    assume(stop > 0)  # risk_manager.evaluate_candidate's own SMA200/price>0 assumptions - a negative stop is nonsensical, not a case this invariant is about
    config = {"risk": {"account_equity": account_equity, "risk_pct_per_trade": risk_pct}}

    position = risk_manager._size_position(entry, stop, config)

    dollar_risk_budget = account_equity * (risk_pct / 100.0)
    assert position["dollar_risk"] <= dollar_risk_budget + 1e-6
    assert position["shares"] >= 0
    assert isinstance(position["shares"], int)


@given(
    entry=st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
    stop=st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
)
@_FAST
def test_position_sizing_is_zero_whenever_stop_is_not_below_entry(entry, stop):
    """Invalid trade geometry (stop >= entry) must never produce a
    positive position size, for any entry/stop combination."""
    assume(stop >= entry)
    config = {"risk": {"account_equity": 10_000.0, "risk_pct_per_trade": 1.0}}
    position = risk_manager._size_position(entry, stop, config)
    assert position["shares"] == 0
    assert position["dollar_risk"] == 0.0


# --- circuit_breaker: threshold checks trip exactly at the configured boundary ----------


_LIMITS = dict(circuit_breaker.DEFAULT_EXECUTION_RISK)


@given(pnl_pct=st.floats(min_value=-1.0, max_value=0.0, allow_nan=False))
@_FAST
def test_daily_loss_breaker_trips_exactly_at_and_beyond_the_configured_limit(pnl_pct):
    """For ANY realized P&L percentage, the breaker's tripped/not-
    tripped decision must exactly match the documented threshold
    (pnl <= -max_daily_loss_pct) - no silent off-by-one or sign error
    for any value in range, not just the hand-picked ones."""
    result = circuit_breaker.check_daily_loss(pnl_pct, _LIMITS)
    should_trip = pnl_pct <= -_LIMITS["max_daily_loss_pct"]
    if should_trip:
        assert result == circuit_breaker.BREAKER_DAILY_LOSS_LIMIT
    else:
        assert result is None


@given(drawdown_pct=st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
@_FAST
def test_drawdown_breaker_trips_exactly_at_and_beyond_the_configured_limit(drawdown_pct):
    result = circuit_breaker.check_drawdown(drawdown_pct, _LIMITS)
    should_trip = drawdown_pct >= _LIMITS["max_drawdown_pct"]
    if should_trip:
        assert result == circuit_breaker.BREAKER_MAX_DRAWDOWN
    else:
        assert result is None


@given(current_open=st.integers(min_value=0, max_value=1000))
@_FAST
def test_max_open_positions_breaker_trips_exactly_at_and_beyond_the_configured_limit(current_open):
    result = circuit_breaker.check_max_open_positions(current_open, _LIMITS)
    should_trip = current_open >= _LIMITS["max_open_positions"]
    if should_trip:
        assert result == circuit_breaker.BREAKER_MAX_OPEN_POSITIONS
    else:
        assert result is None


@given(
    existing_max_positions=st.integers(min_value=1, max_value=100),
    existing_max_risk_pct=st.floats(min_value=0.001, max_value=1.0, allow_nan=False),
)
@_FAST
def test_effective_limits_are_never_looser_than_a_pre_existing_portfolio_risk_limit(existing_max_positions, existing_max_risk_pct):
    """Part K's own rule, fuzzed across the whole input space rather
    than one hand-picked pair of before/after numbers: for ANY
    pre-existing portfolio_risk limit, the effective (merged) limit
    must never come out looser than it."""
    config = {"portfolio_risk": {"max_open_positions": existing_max_positions, "max_total_open_risk_pct": existing_max_risk_pct}}
    effective = circuit_breaker.effective_execution_risk_limits(config)
    assert effective["max_open_positions"] <= existing_max_positions
    assert effective["max_total_open_risk_pct"] <= existing_max_risk_pct
