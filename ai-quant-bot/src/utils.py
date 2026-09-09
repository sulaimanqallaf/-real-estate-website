"""Shared helpers: config loading, logging, and per-symbol error isolation."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

T = TypeVar("T")


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load config/settings.yaml into a plain dict."""
    path = Path(config_path) if config_path else PROJECT_ROOT / "config" / "settings.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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


def setup_logging(config: dict[str, Any]) -> logging.Logger:
    level_name = config.get("logging", {}).get("level", "INFO")
    log_dir = PROJECT_ROOT / config.get("logging", {}).get("log_dir", "data/reports")
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("ai_quant_bot")
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_dir / "app.log")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def resolve_path(relative_path: str) -> Path:
    """Resolve a config-relative path (e.g. 'data/raw') against the project root."""
    return PROJECT_ROOT / relative_path


def safe_run(logger: logging.Logger, symbol: str, func: Callable[[], T]) -> T | None:
    """Run func() and swallow/log any exception so one bad symbol never kills the run."""
    try:
        return func()
    except Exception as exc:  # noqa: BLE001 - intentionally broad, this is the isolation boundary
        logger.error("Skipping %s due to error: %s", symbol, exc, exc_info=True)
        return None
