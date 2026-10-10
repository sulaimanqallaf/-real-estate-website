"""src/execution/connectivity_preflight.py - guided IBKR Paper
broker-connectivity validation workflow (Sprint 3, "IBKR Paper
Readiness" milestone, Task I1). Every check function is tested
against `broker.FakeBroker` (the same fake every other execution-layer
test in this repo uses) or, for the kill switch, a tmp_path-isolated
`execution.halt_state_file` - never a real TWS/Gateway, since none is
reachable from this sandbox (see the module's own docstring and
docs/platform/BLOCKERS.md item 1)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import circuit_breaker, connectivity_preflight as cp
from src.execution.broker import ACCOUNT_MODE_LIVE, ACCOUNT_MODE_PAPER, CONNECTION_CONNECTED, CONNECTION_DISCONNECTED, FakeBroker

LOGGER = logging.getLogger("test")


def _halt_config(tmp_path):
    return {"execution": {"halt_state_file": str(tmp_path / "trading_halt.json")}}


# --- check_paper_identity ------------------------------------------------------------------


def test_paper_identity_passes_for_a_real_paper_account():
    broker = FakeBroker(account_mode=ACCOUNT_MODE_PAPER)
    result = cp.check_paper_identity(broker)
    assert result.status == cp.STATUS_PASS
    assert "PAPER" in result.detail


def test_paper_identity_fails_closed_for_a_live_account():
    broker = FakeBroker(account_mode=ACCOUNT_MODE_LIVE)
    result = cp.check_paper_identity(broker)
    assert result.status == cp.STATUS_FAIL
    assert "LIVE" in result.detail


def test_paper_identity_fails_when_account_summary_raises():
    class _BrokenBroker(FakeBroker):
        def account_summary(self):
            raise RuntimeError("simulated connection drop")

    result = cp.check_paper_identity(_BrokenBroker())
    assert result.status == cp.STATUS_FAIL
    assert "simulated connection drop" in result.detail


# --- check_reconnect_handling -------------------------------------------------------------


class _ScriptedClock:
    """Deterministic fake clock/sleep pair - advances a shared counter
    by `poll_interval_seconds` on every `sleep_fn` call, so the
    while-loop's timeout math is exercised exactly, with zero real
    wall-clock time spent in the test."""

    def __init__(self):
        self.now = 0.0

    def clock_fn(self):
        return self.now

    def sleep_fn(self, seconds):
        self.now += seconds


class _ScriptedConnectionBroker(FakeBroker):
    """A FakeBroker whose connection_state() returns the next value
    from a scripted sequence each call (holding the last value once
    exhausted) - simulates a real human manually stopping/restarting
    their TWS session mid-check."""

    def __init__(self, states: list[str]):
        super().__init__()
        self._states = list(states)
        self._index = 0

    def connection_state(self):
        state = self._states[min(self._index, len(self._states) - 1)]
        self._index += 1
        return state


def test_reconnect_handling_passes_on_a_real_disconnect_then_reconnect():
    broker = _ScriptedConnectionBroker([CONNECTION_CONNECTED, CONNECTION_CONNECTED, CONNECTION_DISCONNECTED, CONNECTION_DISCONNECTED, CONNECTION_CONNECTED])
    clock = _ScriptedClock()

    result = cp.check_reconnect_handling(
        broker, LOGGER, timeout_seconds=60.0, poll_interval_seconds=2.0,
        sleep_fn=clock.sleep_fn, clock_fn=clock.clock_fn, print_fn=lambda *_: None,
    )

    assert result.status == cp.STATUS_PASS
    assert "disconnect and reconnect" in result.detail


def test_reconnect_handling_is_skipped_when_no_disconnect_is_ever_observed():
    broker = _ScriptedConnectionBroker([CONNECTION_CONNECTED])  # never changes
    clock = _ScriptedClock()

    result = cp.check_reconnect_handling(
        broker, LOGGER, timeout_seconds=10.0, poll_interval_seconds=2.0,
        sleep_fn=clock.sleep_fn, clock_fn=clock.clock_fn, print_fn=lambda *_: None,
    )

    assert result.status == cp.STATUS_SKIPPED
    assert "manually stop/restart" in result.detail


def test_reconnect_handling_fails_when_disconnected_and_never_reconnects():
    broker = _ScriptedConnectionBroker([CONNECTION_CONNECTED, CONNECTION_DISCONNECTED])  # stays disconnected
    clock = _ScriptedClock()

    result = cp.check_reconnect_handling(
        broker, LOGGER, timeout_seconds=10.0, poll_interval_seconds=2.0,
        sleep_fn=clock.sleep_fn, clock_fn=clock.clock_fn, print_fn=lambda *_: None,
    )

    assert result.status == cp.STATUS_FAIL
    assert "never reconnected" in result.detail


def test_reconnect_handling_prints_guidance_and_state_transitions():
    broker = _ScriptedConnectionBroker([CONNECTION_CONNECTED, CONNECTION_DISCONNECTED, CONNECTION_CONNECTED])
    clock = _ScriptedClock()
    printed = []

    cp.check_reconnect_handling(
        broker, LOGGER, timeout_seconds=10.0, poll_interval_seconds=2.0,
        sleep_fn=clock.sleep_fn, clock_fn=clock.clock_fn, print_fn=printed.append,
    )

    assert any("manually STOP" in line for line in printed)
    assert any("connection_state ->" in line for line in printed)


# --- check_order_reconciliation ------------------------------------------------------------


def test_order_reconciliation_passes_cleanly_against_an_empty_paper_account():
    broker = FakeBroker()
    result = cp.check_order_reconciliation(broker, LOGGER)
    assert result.status == cp.STATUS_PASS
    assert "zero discrepancies" in result.detail or "Reconciliation clean" in result.detail


def test_order_reconciliation_still_passes_mechanism_check_with_real_discrepancies():
    from src.execution.broker import BrokerPosition

    broker = FakeBroker()
    broker._positions["AAPL"] = BrokerPosition(ticker="AAPL", quantity=10, avg_cost=150.0)

    result = cp.check_order_reconciliation(broker, LOGGER, local_open_trades=[], local_open_orders=[])

    assert result.status == cp.STATUS_PASS  # mechanism working is what this step verifies
    assert "discrepancy" in result.detail.lower()
    assert "not itself a failure" in result.detail


def test_order_reconciliation_fails_when_reconcile_itself_raises(monkeypatch):
    from src.execution import connectivity_preflight as cp_module

    def _raise(*a, **k):
        raise RuntimeError("simulated broker call failure")

    monkeypatch.setattr(cp_module.reconciliation, "reconcile", _raise)
    result = cp.check_order_reconciliation(FakeBroker(), LOGGER)
    assert result.status == cp.STATUS_FAIL
    assert "simulated broker call failure" in result.detail


# --- check_kill_switch ---------------------------------------------------------------------


def test_kill_switch_round_trip_passes_and_restores_the_original_not_halted_state(tmp_path):
    config = _halt_config(tmp_path)
    assert circuit_breaker.is_halted(config) == (False, None)

    result = cp.check_kill_switch(config, LOGGER)

    assert result.status == cp.STATUS_PASS
    assert circuit_breaker.is_halted(config) == (False, None)  # restored, not left halted


def test_kill_switch_skips_without_disturbing_a_real_pre_existing_halt(tmp_path):
    config = _halt_config(tmp_path)
    circuit_breaker.halt(config, reason="a real, intentional halt the user set")

    result = cp.check_kill_switch(config, LOGGER)

    assert result.status == cp.STATUS_SKIPPED
    assert "currently ACTIVE" in result.detail
    halted, reason = circuit_breaker.is_halted(config)
    assert halted is True
    assert reason == "a real, intentional halt the user set"  # untouched


def test_kill_switch_always_resumes_even_if_an_assertion_mid_test_fails(tmp_path, monkeypatch):
    config = _halt_config(tmp_path)

    # Simulate check_manual_kill_switch misbehaving (not reporting a
    # blocking reason while genuinely halted) - the test itself should
    # still end up with the halt file cleared, proving the `finally`
    # restores state even on a FAIL path.
    monkeypatch.setattr(circuit_breaker, "check_manual_kill_switch", lambda cfg: None)

    result = cp.check_kill_switch(config, LOGGER)

    assert result.status == cp.STATUS_FAIL
    assert circuit_breaker.is_halted(config) == (False, None)  # still restored


# --- PreflightReport / format_preflight_report_text ----------------------------------------


def test_report_properties_reflect_each_status():
    report = cp.PreflightReport(
        steps=[
            cp.PreflightStepResult("a", cp.STATUS_PASS, "ok"),
            cp.PreflightStepResult("b", cp.STATUS_FAIL, "bad"),
            cp.PreflightStepResult("c", cp.STATUS_BLOCKED, "no connection"),
            cp.PreflightStepResult("d", cp.STATUS_SKIPPED, "manual step not done"),
        ]
    )
    assert report.any_failed is True
    assert report.any_blocked is True
    assert report.any_skipped is True


def test_format_text_reports_failure_result_line():
    report = cp.PreflightReport(steps=[cp.PreflightStepResult("a", cp.STATUS_FAIL, "bad")])
    text = cp.format_preflight_report_text(report)
    assert "FAILED" in text
    assert "autonomous order submission" in text.lower()


def test_format_text_reports_blocked_result_line_when_no_failures():
    report = cp.PreflightReport(steps=[cp.PreflightStepResult("a", cp.STATUS_PASS, "ok"), cp.PreflightStepResult("b", cp.STATUS_BLOCKED, "no TWS")])
    text = cp.format_preflight_report_text(report)
    assert "BLOCKED" in text
    assert "own machine" in text


def test_format_text_reports_all_passed_when_nothing_blocked_skipped_or_failed():
    report = cp.PreflightReport(steps=[cp.PreflightStepResult("a", cp.STATUS_PASS, "ok")])
    text = cp.format_preflight_report_text(report)
    assert "every check passed" in text


def test_format_text_never_implies_autonomous_execution_got_enabled():
    report = cp.PreflightReport(steps=[cp.PreflightStepResult("a", cp.STATUS_PASS, "ok")])
    text = cp.format_preflight_report_text(report)
    assert "remains OFF" in text


# --- CLI entry point: the BLOCKED path (no real TWS reachable from this sandbox) ------------


def test_main_reports_blocked_steps_and_still_runs_the_kill_switch_when_connect_fails(tmp_path, monkeypatch):
    from src.execution import ibkr_client

    class _AlwaysFailsToConnect:
        def __init__(self, config):
            pass

        def connect(self):
            raise ibkr_client.IBKRConnectionError("simulated: no TWS/Gateway reachable from this sandbox")

    monkeypatch.setattr(ibkr_client, "IBKRClient", _AlwaysFailsToConnect)
    monkeypatch.setattr(ibkr_client.IBKRConfig, "from_env", classmethod(lambda cls: ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id="", expected_account_mode="PAPER")))

    config = {"execution": {"halt_state_file": str(tmp_path / "trading_halt.json")}, "logging": {"log_dir": str(tmp_path / "logs")}}
    monkeypatch.setattr(sys, "argv", ["connectivity_preflight.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config)

    exit_code = cp.main()

    assert exit_code == 0  # BLOCKED is not a FAIL
