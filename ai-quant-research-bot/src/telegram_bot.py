"""Minimal Telegram Bot API client (plain requests, no extra SDK dependency).

This module is a generic Telegram API wrapper only - it knows nothing about paper
trades, candidates, or callback_data formats. That domain logic lives in
paper_trades.py, which hands this module a plain `reply_markup` dict to send.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_HARD_LIMIT_CHARS = 4096
TELEGRAM_CALLBACK_ANSWER_MAX_CHARS = 200


def send_telegram_message(token: str, chat_id: str, text: str, logger: logging.Logger) -> bool:
    """Send one message. Returns True on success, False on failure (never raises)."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


def chunk_message(text: str, max_chars: int) -> list[str]:
    """Split a long report into Telegram-safe chunks, breaking on blank lines when possible."""
    max_chars = min(max_chars, TELEGRAM_HARD_LIMIT_CHARS)
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    blocks = text.split("\n\n")
    current = ""

    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > max_chars:
            if current:
                chunks.append(current)
            current = block
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


def send_report(token: str, chat_id: str, report_text: str, max_message_chars: int, logger: logging.Logger) -> bool:
    """Send the full report, splitting into multiple messages if needed."""
    chunks = chunk_message(report_text, max_message_chars)
    all_ok = True
    for i, chunk in enumerate(chunks, start=1):
        prefix = f"[{i}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        ok = send_telegram_message(token, chat_id, prefix + chunk, logger)
        all_ok = all_ok and ok
    return all_ok


def send_message_with_keyboard(
    token: str, chat_id: str, text: str, keyboard: dict[str, Any], logger: logging.Logger
) -> int | None:
    """Send one message with an inline keyboard. Returns the sent message_id (needed
    to edit it later) on success, or None on failure (never raises)."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "reply_markup": keyboard,
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json()["result"]["message_id"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        logger.error("Failed to send Telegram message with keyboard: %s", exc)
        return None


def edit_message_text(token: str, chat_id: str, message_id: int, text: str, logger: logging.Logger) -> bool:
    """Rewrite an already-sent message (used to show a decision and drop the buttons)."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("Failed to edit Telegram message %s: %s", message_id, exc)
        return False


def get_updates(token: str, offset: int, timeout: int, logger: logging.Logger) -> list[dict[str, Any]]:
    """Long-poll Telegram's getUpdates. `offset` should be the last processed
    update_id + 1 so already-handled updates are never redelivered."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/getUpdates"
    params = {"offset": offset, "timeout": timeout}
    try:
        response = requests.get(url, params=params, timeout=timeout + 10)
        response.raise_for_status()
        return response.json().get("result", [])
    except requests.RequestException as exc:
        logger.error("Failed to poll Telegram getUpdates: %s", exc)
        return []


def answer_callback_query(token: str, callback_query_id: str, text: str, logger: logging.Logger) -> bool:
    """Acknowledge a button press so Telegram stops showing the loading spinner,
    and pop a small toast with `text` on the user's device."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id, "text": text[:TELEGRAM_CALLBACK_ANSWER_MAX_CHARS]}
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("Failed to answer Telegram callback query: %s", exc)
        return False
