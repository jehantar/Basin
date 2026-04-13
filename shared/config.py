"""Basin configuration — reads from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    teller_access_token: str = ""
    teller_cert_path: str = ""
    teller_key_path: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    nasdaq_data_link_api_key: str = ""
    strava_client_id: str = ""
    strava_client_secret: str = ""


def load_config() -> Config:
    return Config(
        database_url=os.environ["DATABASE_URL"],
        teller_access_token=os.environ.get("TELLER_ACCESS_TOKEN", ""),
        teller_cert_path=os.environ.get("TELLER_CERT_PATH", ""),
        teller_key_path=os.environ.get("TELLER_KEY_PATH", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        nasdaq_data_link_api_key=os.environ.get("NASDAQ_DATA_LINK_API_KEY", ""),
        strava_client_id=os.environ.get("STRAVA_CLIENT_ID", ""),
        strava_client_secret=os.environ.get("STRAVA_CLIENT_SECRET", ""),
    )
