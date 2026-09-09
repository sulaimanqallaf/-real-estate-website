"""Phase 7 Part Z - Connection safety: paper accepted, live rejected, unknown
rejected, missing account identity fails closed, live PORT blocks regardless
of what the account reports, and there is no override flag anywhere in
`verify_paper_account`'s signature.
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import ibkr_client
from src.execution.broker import ACCOUNT_MODE_LIVE, ACCOUNT_MODE_PAPER, ACCOUNT_MODE_UNKNOWN


def test_paper_account_on_paper_port_is_accepted():
    summary = ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_PAPER, configured_port=7497)
    assert summary.account_mode == ACCOUNT_MODE_PAPER
    assert summary.account_id == "DU123456"


def test_live_account_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="LIVE_ACCOUNT_BLOCKED"):
        ibkr_client.verify_paper_account("U123456", ACCOUNT_MODE_LIVE, configured_port=7497)


def test_unknown_account_mode_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_UNKNOWN, configured_port=7497)


def test_missing_reported_mode_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("DU123456", None, configured_port=7497)


def test_missing_account_id_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account(None, ACCOUNT_MODE_PAPER, configured_port=7497)


def test_empty_string_account_id_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("", ACCOUNT_MODE_PAPER, configured_port=7497)


def test_unrecognized_reported_mode_string_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("DU123456", "SOMETHING_ELSE", configured_port=7497)


@pytest.mark.parametrize("live_port", [ibkr_client.DEFAULT_TWS_LIVE_PORT, ibkr_client.DEFAULT_GATEWAY_LIVE_PORT])
def test_live_port_blocks_even_if_account_claims_paper(live_port):
    """Defense-in-depth (Part B): a misconfigured port is itself a live-
    trading risk - it must block regardless of what the account query
    would have said, because a real IBKR session bound to a live port IS
    a live-trading session."""
    with pytest.raises(ibkr_client.AccountModeError, match="LIVE_ACCOUNT_BLOCKED"):
        ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_PAPER, configured_port=live_port)


@pytest.mark.parametrize("paper_port", [ibkr_client.DEFAULT_TWS_PAPER_PORT, ibkr_client.DEFAULT_GATEWAY_PAPER_PORT])
def test_documented_paper_ports_are_not_blocked_by_port_check(paper_port):
    summary = ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_PAPER, configured_port=paper_port)
    assert summary.account_mode == ACCOUNT_MODE_PAPER


def test_non_paper_expected_mode_is_rejected_there_is_no_supported_alternative():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_PAPER, configured_port=7497, expected_mode=ACCOUNT_MODE_LIVE)


def test_verify_paper_account_signature_has_no_override_parameter():
    """Programmatically prove there is no force/override/allow_live
    parameter anywhere on this function - the one thing Part B explicitly
    forbids."""
    params = set(inspect.signature(ibkr_client.verify_paper_account).parameters.keys())
    forbidden = {"force", "override", "allow_live", "skip_check", "bypass"}
    assert not (params & forbidden)


def test_classify_account_id_heuristic_du_prefix_is_paper():
    assert ibkr_client.classify_account_id("DU123456") == ACCOUNT_MODE_PAPER


def test_classify_account_id_heuristic_df_prefix_is_paper():
    assert ibkr_client.classify_account_id("DF987654") == ACCOUNT_MODE_PAPER


def test_classify_account_id_heuristic_u_prefix_is_live():
    assert ibkr_client.classify_account_id("U123456") == ACCOUNT_MODE_LIVE


def test_classify_account_id_heuristic_unrecognized_prefix_is_unknown():
    assert ibkr_client.classify_account_id("XYZ123") == ACCOUNT_MODE_UNKNOWN


def test_ibkr_config_from_env_defaults_to_paper_port_and_mode(monkeypatch):
    monkeypatch.delenv("IBKR_HOST", raising=False)
    monkeypatch.delenv("IBKR_PORT", raising=False)
    monkeypatch.delenv("IBKR_CLIENT_ID", raising=False)
    monkeypatch.delenv("IBKR_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("IBKR_EXPECTED_ACCOUNT_MODE", raising=False)
    config = ibkr_client.IBKRConfig.from_env()
    assert config.host == "127.0.0.1"
    assert config.port == ibkr_client.DEFAULT_TWS_PAPER_PORT
    assert config.expected_account_mode == ACCOUNT_MODE_PAPER


def test_ibkr_client_never_reaches_connected_without_verification(monkeypatch):
    """connect() must raise before _state is ever set to CONNECTED if
    _build_app() itself fails (as it always will here - see module
    docstring: no real ibapi/TWS socket is reachable in this sandbox)."""
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError):
        client.connect()
    assert client.connection_state() != "CONNECTED"


def test_ibkr_client_account_summary_raises_before_any_connect():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        client.account_summary()


def test_ibkr_client_submit_order_refuses_when_not_connected():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError):
        client.submit_order(object())
