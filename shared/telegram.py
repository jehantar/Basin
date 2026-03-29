"""Telegram alert helper — sends messages via the Bot API."""

import logging

import httpx

from shared.config import load_config

logger = logging.getLogger("basin.telegram")

TELEGRAM_API = "https://api.telegram.org"


def send_alert(message: str, prefix: str = "[Basin]") -> bool:
    """
    Send a message to the configured Telegram chat.
    Returns True on success, False on failure (logs the error).
    """
    config = load_config()
    if not config.telegram_bot_token or not config.telegram_chat_id:
        logger.warning("Telegram not configured, skipping alert")
        return False

    url = f"{TELEGRAM_API}/bot{config.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": config.telegram_chat_id,
        "text": f"{prefix} {message}",
        "parse_mode": "Markdown",
    }

    try:
        resp = httpx.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error(f"Telegram alert failed: {e}")
        return False
