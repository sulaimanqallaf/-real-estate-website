"""src/agents/launchd_installer.py - the Sprint 5 opt-in launchd
schedule installer. Darwin-only side effects (install/uninstall) are
tested by forcing is_darwin() to False (this sandbox is Linux, so that
path is exercised for real too) and by monkeypatching subprocess.run
for the macOS-only branches; the safety assertions (never touch the 7
existing jobs) are tested directly, with no mocking needed."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.agents import launchd_installer as li

LOGGER = logging.getLogger("test")


# --- safety: the 7 existing jobs are untouchable -----------------------------------------------


def test_default_job_specs_never_collide_with_a_reserved_label():
    default_labels = {spec.label for spec in li.DEFAULT_JOB_SPECS}
    assert default_labels.isdisjoint(li.RESERVED_EXISTING_LABELS)


@pytest.mark.parametrize("label", sorted(li.RESERVED_EXISTING_LABELS))
def test_assert_label_is_new_rejects_every_reserved_label(label):
    with pytest.raises(li.ReservedLabelError):
        li.assert_label_is_new(label)


def test_assert_label_is_new_allows_a_default_job_label():
    li.assert_label_is_new(li.LABEL_RESEARCH_SCHOLAR)  # must not raise


@pytest.mark.parametrize("label", sorted(li.RESERVED_EXISTING_LABELS))
def test_plist_path_rejects_a_reserved_label(label):
    with pytest.raises(li.ReservedLabelError):
        li.plist_path(label)


@pytest.mark.parametrize("label", sorted(li.RESERVED_EXISTING_LABELS))
def test_install_rejects_a_reserved_label(tmp_path, label):
    fake_spec = li.JobSpec("x", label, "src.agents.research_scholar", "x", calendar_hour=1, calendar_minute=0)
    with pytest.raises(li.ReservedLabelError):
        li.install(fake_spec, tmp_path / "python", tmp_path, tmp_path / "logs", LOGGER)


@pytest.mark.parametrize("label", sorted(li.RESERVED_EXISTING_LABELS))
def test_uninstall_rejects_a_reserved_label(label):
    with pytest.raises(li.ReservedLabelError):
        li.uninstall(label, LOGGER)


@pytest.mark.parametrize("label", sorted(li.RESERVED_EXISTING_LABELS))
def test_is_installed_rejects_a_reserved_label(label):
    with pytest.raises(li.ReservedLabelError):
        li.is_installed(label, LOGGER)


# --- generate_plist: pure string generation, no credential ------------------------------------


def test_generate_plist_includes_label_and_module(tmp_path):
    spec = li.DEFAULT_JOB_SPECS[0]
    text = li.generate_plist(spec, tmp_path / ".venv" / "bin" / "python", tmp_path, tmp_path / "logs")
    assert f"<string>{spec.label}</string>" in text
    assert f"<string>{spec.module}</string>" in text
    assert "<string>-m</string>" in text


def test_generate_plist_uses_start_calendar_interval_for_a_daily_job(tmp_path):
    spec = li.JobSpec("x", li.LABEL_RESEARCH_SCHOLAR, "src.agents.research_scholar", "x", calendar_hour=6, calendar_minute=30)
    text = li.generate_plist(spec, tmp_path / "python", tmp_path, tmp_path / "logs")
    assert "StartCalendarInterval" in text
    assert "<integer>6</integer>" in text
    assert "<integer>30</integer>" in text
    assert "StartInterval" not in text


def test_generate_plist_uses_start_interval_for_a_periodic_job(tmp_path):
    spec = li.JobSpec("x", li.LABEL_AGENT_FLEET_HEALTH, "src.agents.health_report", "x", interval_seconds=1800)
    text = li.generate_plist(spec, tmp_path / "python", tmp_path, tmp_path / "logs")
    assert "<key>StartInterval</key>" in text
    assert "<integer>1800</integer>" in text
    assert "StartCalendarInterval" not in text


def test_generate_plist_never_embeds_a_credential(tmp_path):
    """No ProgramArguments entry beyond [python, -m, module] - the
    agent reads .env itself; nothing here ever carries a secret."""
    spec = li.DEFAULT_JOB_SPECS[0]
    text = li.generate_plist(spec, tmp_path / "python", tmp_path, tmp_path / "logs")
    for forbidden in ("TELEGRAM_BOT_TOKEN", "ALPACA_API", "SEC_IDENTITY", "FRED_API_KEY", "IBKR_"):
        assert forbidden not in text


def test_generate_plist_rejects_a_reserved_label(tmp_path):
    fake_spec = li.JobSpec("x", "com.aiquantresearchbot.daily", "src.main", "x", calendar_hour=1, calendar_minute=0)
    with pytest.raises(li.ReservedLabelError):
        li.generate_plist(fake_spec, tmp_path / "python", tmp_path, tmp_path / "logs")


# --- macOS-gating: everything is inert off Darwin ----------------------------------------------


def test_install_is_a_noop_off_darwin(tmp_path, monkeypatch):
    monkeypatch.setattr(li, "is_darwin", lambda: False)
    spec = li.DEFAULT_JOB_SPECS[0]
    assert li.install(spec, tmp_path / "python", tmp_path, tmp_path / "logs", LOGGER) is False
    assert not li.plist_path(spec.label).exists()


def test_uninstall_is_a_noop_off_darwin(monkeypatch):
    monkeypatch.setattr(li, "is_darwin", lambda: False)
    assert li.uninstall(li.LABEL_RESEARCH_SCHOLAR, LOGGER) is False


def test_is_installed_is_unknown_off_darwin(monkeypatch):
    monkeypatch.setattr(li, "is_darwin", lambda: False)
    assert li.is_installed(li.LABEL_RESEARCH_SCHOLAR, LOGGER) is None


# --- install/uninstall on a simulated Darwin (subprocess mocked) -------------------------------


def test_install_writes_the_plist_and_calls_launchctl_load(tmp_path, monkeypatch):
    monkeypatch.setattr(li, "is_darwin", lambda: True)
    monkeypatch.setattr(li.Path, "home", lambda: tmp_path)

    captured = {}

    class _FakeResult:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeResult()

    monkeypatch.setattr(li.subprocess, "run", _fake_run)

    spec = li.DEFAULT_JOB_SPECS[0]
    ok = li.install(spec, tmp_path / "python", tmp_path, tmp_path / "logs", LOGGER)
    assert ok is True
    assert captured["cmd"][:2] == ["launchctl", "load"]
    assert li.plist_path(spec.label).exists()
    assert spec.label in li.plist_path(spec.label).read_text(encoding="utf-8")


def test_install_returns_false_when_launchctl_load_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(li, "is_darwin", lambda: True)
    monkeypatch.setattr(li.Path, "home", lambda: tmp_path)

    class _FakeResult:
        returncode = 1
        stderr = "simulated failure"

    monkeypatch.setattr(li.subprocess, "run", lambda cmd, **kwargs: _FakeResult())

    spec = li.DEFAULT_JOB_SPECS[0]
    assert li.install(spec, tmp_path / "python", tmp_path, tmp_path / "logs", LOGGER) is False


def test_uninstall_removes_the_plist_file(tmp_path, monkeypatch):
    monkeypatch.setattr(li, "is_darwin", lambda: True)
    monkeypatch.setattr(li.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(li.subprocess, "run", lambda cmd, **kwargs: None)

    path = li.plist_path(li.LABEL_RESEARCH_SCHOLAR)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("placeholder", encoding="utf-8")

    assert li.uninstall(li.LABEL_RESEARCH_SCHOLAR, LOGGER) is True
    assert not path.exists()


def test_uninstall_is_idempotent_when_nothing_is_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(li, "is_darwin", lambda: True)
    monkeypatch.setattr(li.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(li.subprocess, "run", lambda cmd, **kwargs: None)

    assert li.uninstall(li.LABEL_RESEARCH_SCHOLAR, LOGGER) is True


# --- default_python_path -------------------------------------------------------------------------


def test_default_python_path_uses_the_root_venv_not_the_dashboard_backend_venv(tmp_path):
    path = li.default_python_path(tmp_path)
    assert path == tmp_path / ".venv" / "bin" / "python"


# --- spec_by_name / main() CLI ------------------------------------------------------------------


def test_spec_by_name_finds_a_known_job():
    assert li.spec_by_name("research_scholar") is li.DEFAULT_JOB_SPECS[0]


def test_spec_by_name_returns_none_for_an_unknown_job():
    assert li.spec_by_name("not_a_real_job") is None


def _patch_main_config(monkeypatch, tmp_path):
    config = {"data": {"journal_dir": str(tmp_path / "journal")}, "logging": {"log_dir": str(tmp_path / "logs")}}
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config)


def test_main_list_never_installs_or_uninstalls_anything(tmp_path, monkeypatch, capsys):
    _patch_main_config(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["launchd_installer.py", "list"])
    monkeypatch.setattr(li, "is_darwin", lambda: False)

    assert li.main() == 0
    out = capsys.readouterr().out
    for spec in li.DEFAULT_JOB_SPECS:
        assert spec.name in out


def test_main_install_requires_a_job_name(tmp_path, monkeypatch):
    _patch_main_config(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["launchd_installer.py", "install"])
    assert li.main() == 1


def test_main_install_rejects_an_unknown_job_name(tmp_path, monkeypatch):
    _patch_main_config(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["launchd_installer.py", "install", "not_a_real_job"])
    assert li.main() == 1


def test_main_install_calls_install_for_a_known_job(tmp_path, monkeypatch):
    _patch_main_config(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["launchd_installer.py", "install", "research_scholar"])

    captured = {}
    monkeypatch.setattr(li, "install", lambda spec, python_path, repo_root, log_dir, logger: captured.setdefault("spec", spec) or True)

    assert li.main() == 0
    assert captured["spec"].name == "research_scholar"


def test_main_uninstall_calls_uninstall_for_a_known_job(tmp_path, monkeypatch):
    _patch_main_config(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["launchd_installer.py", "uninstall", "news_agent"])

    captured = {}
    monkeypatch.setattr(li, "uninstall", lambda label, logger: captured.setdefault("label", label) or True)

    assert li.main() == 0
    assert captured["label"] == li.LABEL_NEWS_AGENT
