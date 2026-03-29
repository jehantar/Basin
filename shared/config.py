"""Basin configuration — reads from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    schwab_client_id: str = ""
    schwab_client_secret: str = ""
    schwab_redirect_uri: str = ""
    teller_access_token: str = ""
    teller_cert_path: str = ""
    teller_key_path: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


def load_config() -> Config:
    return Config(
        database_url=os.environ["DATABASE_URL"],
        schwab_client_id=os.environ.get("SCHWAB_CLIENT_ID", ""),
        schwab_client_secret=os.environ.get("SCHWAB_CLIENT_SECRET", ""),
        schwab_redirect_uri=os.environ.get("SCHWAB_REDIRECT_URI", ""),
        teller_access_token=os.environ.get("TELLER_ACCESS_TOKEN", ""),
        teller_cert_path=os.environ.get("TELLER_CERT_PATH", ""),
        teller_key_path=os.environ.get("TELLER_KEY_PATH", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
