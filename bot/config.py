"""
config.py — Load and expose all environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Bot token from @BotFather
BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# Telegram API credentials
API_ID: int = int(os.environ["API_ID"])
API_HASH: str = os.environ["API_HASH"]

# Admin user IDs (comma-separated in .env)
ADMIN_IDS: list[int] = [
    int(uid.strip()) for uid in os.environ["ADMIN_IDS"].split(",") if uid.strip()
]

# Path to the JSON accounts database
DB_PATH: str = os.getenv("DB_PATH", "data/accounts.json")

# Directory where per-account session files are stored
SESSIONS_DIR: str = os.getenv("SESSIONS_DIR", "sessions")
