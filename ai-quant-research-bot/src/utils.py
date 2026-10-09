"""Shared helpers: config loading, logging, and per-symbol error isolation."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, TypeVar

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

T = TypeVar("T")

# Shared secret-redaction patterns - originally lived only in
# `intelligence/tradingagents_adapter.py` (an upstream LLM provider's
# error text occasionally echoes the key it rejected), promoted here so
# any other module that must log/alert on an arbitrary exception string
# (e.g. `execution/run_health.py`'s Telegram failure alert) gets the same
# protection instead of re-inventing or forgetting it. Never assume any
# upstream or internal error string is already safe to log/send verbatim.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(api[_-]?key|authorization|token)\s*[:=]\s*\S+"),
]


def redact_secrets(text: str | None) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted

# The ONLY two execution modes this codebase supports, anywhere - see
# _apply_execution_mode_override() below. There is no IBKR_LIVE mode, and
# nothing (env var included) can introduce one.
VALID_EXECUTION_MODES = ("DRY_RUN", "IBKR_PAPER")


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load config/settings.yaml into a plain dict.

    `EXECUTION_MODE` in the environment (.env), if set, overrides
    `execution.mode` at runtime - see `_apply_execution_mode_override()`.
    This is the intended way to switch into IBKR_PAPER for local manual
    testing: a one-line `.env` change, never a hand-edit to
    config/settings.yaml, which stays the version-controlled, always-safe
    default (DRY_RUN) for everyone who clones this repo.
    """
    path = Path(config_path) if config_path else PROJECT_ROOT / "config" / "settings.yaml"
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    _apply_execution_mode_override(config)
    return config


def _apply_execution_mode_override(config: dict[str, Any]) -> None:
    """Mutates `config["execution"]["mode"]` in place if `EXECUTION_MODE`
    is set in the environment. Only `VALID_EXECUTION_MODES` are accepted -
    anything else (a typo, "IBKR_LIVE", an old/unsupported value) raises
    immediately rather than silently falling back to the config file's
    own value or guessing: a misconfigured environment variable failing
    loudly, at startup, is far safer than this system quietly running in
    a mode nobody actually asked for."""
    raw = os.environ.get("EXECUTION_MODE")
    if not raw:
        return
    if raw not in VALID_EXECUTION_MODES:
        raise ValueError(
            f"Invalid EXECUTION_MODE={raw!r} in the environment - only {VALID_EXECUTION_MODES} "
            "are supported. There is no IBKR_LIVE mode in this codebase, and none can be added "
            "via this override."
        )
    config.setdefault("execution", {})["mode"] = raw


def load_env() -> None:
    """Load variables from .env into the process environment, if present."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def get_env_var(name: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable '{name}'. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def setup_logging(config: dict[str, Any], log_filename: str = "app.log") -> logging.Logger:
    level_name = config.get("logging", {}).get("level", "INFO")
    log_dir = PROJECT_ROOT / config.get("logging", {}).get("log_dir", "data/reports")
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("ai_quant_research_bot")
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_dir / log_filename)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def resolve_path(relative_path: str) -> Path:
    """Resolve a config-relative path (e.g. 'data/raw') against the project root."""
    return PROJECT_ROOT / relative_path


def safe_run(logger: logging.Logger, label: str, func: Callable[[], T]) -> T | None:
    """Run func() and swallow/log any exception so one bad symbol/step never kills the run."""
    try:
        return func()
    except Exception as exc:  # noqa: BLE001 - intentionally broad, this is the isolation boundary
        logger.error("Skipping %s due to error: %s", label, exc, exc_info=True)
        return None


def is_nan(value: Any) -> bool:
    return value is None or (isinstance(value, float) and value != value)
