"""Standalone, continuously-running listener for the paper-trade approval buttons.

Run this SEPARATELY from the daily `python -m src.main` batch job - in its own
terminal/tmux session, or as a launchd agent with KeepAlive (see README section
"How to run the paper-trade approval listener"). It never places real trades; it
only records approvals into data/journal/paper_trades.csv via paper_trades.py.

Why a separate process: main.py is a once-a-day batch job invoked by cron/launchd
and exits when done. Telegram button presses can happen minutes or hours later,
whenever you actually check your phone, so something has to be listening on its
own schedule for that - a long-poll loop against Telegram's getUpdates, persisting
its offset to disk so a restart never redelivers (and double-processes) old
button presses.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from . import paper_trades, telegram_bot
from .utils import get_env_var, load_config, load_env, resolve_path, setup_logging


def _offset_path(config: dict[str, Any]) -> Path:
    return resolve_path(config["data"]["journal_dir"]) / config["paper_trading"]["update_offset_file"]


def _load_offset(config: dict[str, Any]) -> int:
    path = _offset_path(config)
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip())
    except ValueError:
        return 0


def _save_offset(config: dict[str, Any], offset: int) -> None:
    path = _offset_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(offset))


def handle_update(update: dict[str, Any], token: str, chat_id: str, config: dict[str, Any], logger) -> None:
    callback_query = update.get("callback_query")
    if not callback_query:
        return  # not a button press (e.g. a plain text message) - nothing to do

    message = callback_query.get("message") or {}
    source_chat_id = str(message.get("chat", {}).get("id", ""))
    if source_chat_id != str(chat_id):
        # Only the configured TELEGRAM_CHAT_ID may approve/reject/watch anything.
        logger.warning("Ignoring callback from unauthorized chat_id=%s", source_chat_id)
        telegram_bot.answer_callback_query(token, callback_query["id"], "Not authorized.", logger)
        return

    decoded = paper_trades.decode_callback_data(callback_query.get("data", ""))
    if decoded is None:
        telegram_bot.answer_callback_query(token, callback_query["id"], "Unrecognized button.", logger)
        return

    action, symbol, report_date = decoded
    _, result_text = paper_trades.process_decision(action, symbol, report_date, config, logger)

    telegram_bot.answer_callback_query(token, callback_query["id"], result_text, logger)

    message_id = message.get("message_id")
    if message_id is not None:
        icon = {"approve": "✅", "reject": "❌", "watch": "👀"}.get(action, "ℹ️")
        original_text = message.get("text", symbol)
        telegram_bot.edit_message_text(token, chat_id, message_id, f"{original_text}\n\n{icon} {result_text}", logger)


def run() -> int:
    load_env()
    config = load_config()
    logger = setup_logging(config, log_filename="approval_listener.log")

    token = get_env_var("TELEGRAM_BOT_TOKEN")
    chat_id = get_env_var("TELEGRAM_CHAT_ID")

    poll_timeout = config["paper_trading"]["poll_timeout_seconds"]
    poll_interval = config["paper_trading"]["poll_interval_seconds"]

    offset = _load_offset(config)
    logger.info("Approval listener started (resuming from update offset %d). Ctrl+C to stop.", offset)

    while True:
        updates = telegram_bot.get_updates(token, offset, poll_timeout, logger)
        for update in updates:
            offset = update["update_id"] + 1
            try:
                handle_update(update, token, chat_id, config, logger)
            except Exception:  # noqa: BLE001 - one bad update must never crash the listener
                logger.exception("Error handling update %s", update.get("update_id"))
            _save_offset(config, offset)
        if not updates:
            time.sleep(poll_interval)


if __name__ == "__main__":
    sys.exit(run())
