"""The bot token must never reach a log line or terminal output. Every
Telegram API URL embeds it as a path segment
(".../bot<token>/method"), and `requests.RequestException`'s own string
representation includes the full request URL - so an exception logged
without redaction leaks the token into approval_listener.log (this is
exactly how the token got exposed in terminal output previously)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import telegram_bot

SECRET_TOKEN = "123456789:AAExampleSecretTokenNeverLogThis"


def test_redact_token_strips_the_token_from_a_url():
    text = f"HTTPSConnectionPool: GET https://api.telegram.org/bot{SECRET_TOKEN}/getUpdates failed"
    redacted = telegram_bot._redact_token(text)
    assert SECRET_TOKEN not in redacted
    assert "REDACTED" in redacted


def test_redact_token_leaves_ordinary_text_untouched():
    text = "Connection timed out after 15s"
    assert telegram_bot._redact_token(text) == text


def _make_recording_logger():
    logger = logging.getLogger("test_telegram_bot_redaction")
    logger.setLevel(logging.ERROR)
    records: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger.handlers = [_Handler()]
    logger.propagate = False
    return logger, records


def test_get_updates_never_logs_the_token_on_failure(monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): "
            f"Max retries exceeded with url: /bot{SECRET_TOKEN}/getUpdates"
        )

    monkeypatch.setattr(telegram_bot.requests, "get", boom)
    logger, records = _make_recording_logger()

    result = telegram_bot.get_updates(SECRET_TOKEN, 0, 10, logger)

    assert result == []
    assert records, "expected an error to have been logged"
    assert all(SECRET_TOKEN not in message for message in records)


def test_send_telegram_message_never_logs_the_token_on_failure(monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError(f"failed: url=/bot{SECRET_TOKEN}/sendMessage")

    monkeypatch.setattr(telegram_bot.requests, "post", boom)
    logger, records = _make_recording_logger()

    result = telegram_bot.send_telegram_message(SECRET_TOKEN, "123", "hi", logger)

    assert result is False
    assert records
    assert all(SECRET_TOKEN not in message for message in records)
