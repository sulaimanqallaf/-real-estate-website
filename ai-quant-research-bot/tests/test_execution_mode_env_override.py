"""EXECUTION_MODE env var override (Phase 7 continuation): local manual
IBKR Paper testing is a one-line .env change, never a hand-edit to
config/settings.yaml - which stays the version-controlled, always-safe
DRY_RUN default. Only "DRY_RUN" and "IBKR_PAPER" are accepted; anything
else (including "IBKR_LIVE") raises immediately rather than silently
falling back or guessing.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import utils

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


@pytest.fixture(autouse=True)
def _clean_execution_mode_env(monkeypatch):
    """Every test in this file controls EXECUTION_MODE explicitly - never
    let whatever happens to be in the real shell/CI environment leak in."""
    monkeypatch.delenv("EXECUTION_MODE", raising=False)


# --- default config/settings.yaml, with no override, stays DRY_RUN -----------------


def test_default_config_file_is_dry_run_with_no_env_override():
    config = utils.load_config(CONFIG_PATH)
    assert config["execution"]["mode"] == "DRY_RUN"


def test_default_config_autonomous_paper_disabled():
    config = utils.load_config(CONFIG_PATH)
    assert config["autonomous_paper"]["enabled"] is False
    assert config["autonomous_paper"]["auto_execute"]["enabled"] is False


# --- EXECUTION_MODE=IBKR_PAPER overrides the file at runtime ------------------------


def test_execution_mode_env_var_overrides_to_ibkr_paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "IBKR_PAPER")
    config = utils.load_config(CONFIG_PATH)
    assert config["execution"]["mode"] == "IBKR_PAPER"


def test_execution_mode_env_var_explicit_dry_run_is_a_no_op(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "DRY_RUN")
    config = utils.load_config(CONFIG_PATH)
    assert config["execution"]["mode"] == "DRY_RUN"


def test_override_does_not_touch_autonomous_paper_flags(monkeypatch):
    """Switching to IBKR_PAPER via .env must never also flip autonomous
    execution on - manual approval only, exactly as documented."""
    monkeypatch.setenv("EXECUTION_MODE", "IBKR_PAPER")
    config = utils.load_config(CONFIG_PATH)
    assert config["autonomous_paper"]["enabled"] is False
    assert config["autonomous_paper"]["auto_execute"]["enabled"] is False


def test_unset_or_empty_env_var_leaves_the_file_value_untouched(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "")
    config = utils.load_config(CONFIG_PATH)
    assert config["execution"]["mode"] == "DRY_RUN"


# --- invalid values raise, loudly, rather than silently falling back ----------------


def test_invalid_execution_mode_raises(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "SOMETHING_ELSE")
    with pytest.raises(ValueError, match="Invalid EXECUTION_MODE"):
        utils.load_config(CONFIG_PATH)


def test_lowercase_execution_mode_is_rejected_not_silently_normalized(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "dry_run")
    with pytest.raises(ValueError):
        utils.load_config(CONFIG_PATH)


def test_typo_execution_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "IBKR_PAPR")
    with pytest.raises(ValueError):
        utils.load_config(CONFIG_PATH)


# --- there must still be no IBKR_LIVE mode, anywhere, ever --------------------------


def test_ibkr_live_is_explicitly_rejected(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "IBKR_LIVE")
    with pytest.raises(ValueError, match="no IBKR_LIVE mode"):
        utils.load_config(CONFIG_PATH)


def test_live_is_rejected(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    with pytest.raises(ValueError):
        utils.load_config(CONFIG_PATH)


def test_valid_execution_modes_constant_has_exactly_two_values_neither_is_live():
    assert utils.VALID_EXECUTION_MODES == ("DRY_RUN", "IBKR_PAPER")
    assert all("LIVE" not in mode for mode in utils.VALID_EXECUTION_MODES)


def test_apply_execution_mode_override_creates_execution_key_if_missing(monkeypatch):
    """A minimal config dict with no "execution" key at all must not
    crash the override - it should just create one."""
    monkeypatch.setenv("EXECUTION_MODE", "IBKR_PAPER")
    config: dict = {}
    utils._apply_execution_mode_override(config)
    assert config["execution"]["mode"] == "IBKR_PAPER"


def test_apply_execution_mode_override_is_a_noop_with_no_env_var():
    config = {"execution": {"mode": "DRY_RUN"}}
    utils._apply_execution_mode_override(config)
    assert config["execution"]["mode"] == "DRY_RUN"


# --- config_path override (used by tests elsewhere) still gets the same treatment ---


def test_override_applies_regardless_of_which_config_path_is_loaded(tmp_path, monkeypatch):
    minimal_yaml = tmp_path / "settings.yaml"
    minimal_yaml.write_text("execution:\n  mode: DRY_RUN\n")
    monkeypatch.setenv("EXECUTION_MODE", "IBKR_PAPER")
    config = utils.load_config(minimal_yaml)
    assert config["execution"]["mode"] == "IBKR_PAPER"


# --- the real .env file -> load_env() -> load_config() pipeline, end to end --------


def test_real_dot_env_file_switches_execution_mode(tmp_path, monkeypatch):
    """Proves the actual mechanism goal 2/3 describe: a plain `.env` file
    on disk, loaded by load_env(), is what load_config() then honors -
    not just os.environ already being set by some other means.

    load_env() calls the real python-dotenv load_dotenv(), which writes
    straight into os.environ via a raw assignment, bypassing monkeypatch
    entirely. `monkeypatch.delenv()` is the WRONG cleanup tool here: it
    records an undo action ("restore the value that was present when
    delenv ran") and replays that undo at this test's own teardown - so
    calling it mid-test would silently RESTORE "IBKR_PAPER" right as the
    test ends, one step after the explicit cleanup appeared to run,
    re-leaking it into every later test in this same pytest process. A
    plain os.environ.pop() has no undo tracking to fight, so it actually
    stays gone."""
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("EXECUTION_MODE=IBKR_PAPER\n")

    try:
        utils.load_env()
        config = utils.load_config(CONFIG_PATH)
        assert config["execution"]["mode"] == "IBKR_PAPER"
    finally:
        os.environ.pop("EXECUTION_MODE", None)


def test_no_dot_env_file_present_leaves_dry_run_default(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)  # no .env written here at all

    try:
        utils.load_env()
        config = utils.load_config(CONFIG_PATH)
        assert config["execution"]["mode"] == "DRY_RUN"
    finally:
        os.environ.pop("EXECUTION_MODE", None)
