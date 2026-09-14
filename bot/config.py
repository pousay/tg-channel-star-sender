"""
config.py — Load and expose all environment variables.
"""

import logging
import os
from dotenv import load_dotenv

load_dotenv()

# Bot token from @BotFather
BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# Telegram API credentials
API_ID: int = int(os.environ["API_ID"])
API_HASH: str = os.environ["API_HASH"]

# Admin user IDs (comma-separated in .env).
# Single source of truth: these admins manage the bot panel AND receive
# every event log (success, failure, summaries, alerts).
ADMIN_IDS: list[int] = [
    int(uid.strip()) for uid in os.environ["ADMIN_IDS"].split(",") if uid.strip()
]

if not ADMIN_IDS:
    logging.warning(
        "ADMIN_IDS is empty — nobody can use the bot panel and no event "
        "logs will be delivered. Fill ADMIN_IDS in .env!"
    )

# Path to the JSON accounts database
DB_PATH: str = os.getenv("DB_PATH", "data/accounts.json")

# Directory where per-account session files are stored
SESSIONS_DIR: str = os.getenv("SESSIONS_DIR", "sessions")

# ── Channel auto-reactions + Star gifting ─────────────────────────────────────


def _parse_channel(raw_value: str) -> int | str:
    """Accept numeric channel IDs (-100...) as int, usernames as str."""
    raw_value = raw_value.strip()
    if raw_value.lstrip("-").isdigit():
        return int(raw_value)
    return raw_value


# Channel to monitor (bot must be an admin there)
TARGET_CHANNEL: int | str = _parse_channel(os.getenv("TARGET_CHANNEL", "@mychannel"))

# Posts are processed by default. Any post whose text/caption contains one of
# these (comma-separated, case-insensitive) markers is treated as an
# advertisement and skipped entirely — no reaction, no stars.
AD_MARKERS: list[str] = [
    m.strip()
    for m in os.getenv("POST_KEYWORD", "#ad,#sponsored,تبلیغ").split(",")
    if m.strip()
]

# Minutes to wait after detecting a qualifying post before acting
DELAY_MINUTES: int = int(os.getenv("DELAY_MINUTES", "13"))

# Random number of accounts to select is drawn between these two
MIN_ACCOUNTS: int = int(os.getenv("MIN_ACCOUNTS", "2"))
MAX_ACCOUNTS: int = int(os.getenv("MAX_ACCOUNTS", "5"))

# Random Star amount per account is drawn between these two.
# An account is only eligible if its balance is >= MAX_STARS.
MIN_STARS: int = int(os.getenv("MIN_STARS", "3"))
MAX_STARS: int = int(os.getenv("MAX_STARS", "5"))

# Allowed reactions (comma-separated emoji list)
REACTIONS: list[str] = [
    r.strip() for r in os.getenv("REACTIONS", "❤️,👍,🔥,🎉").split(",") if r.strip()
]
