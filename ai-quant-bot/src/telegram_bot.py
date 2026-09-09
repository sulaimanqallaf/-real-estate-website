"""Minimal Telegram Bot API client (plain requests, no extra SDK dependency)."""

from __future__ import annotations

import logging
from typing import Any

import requests

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_HARD_LIMIT_CHARS = 4096


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
