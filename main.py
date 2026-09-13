"""
main.py — Entry point for the Star Sender bot.

Creates the bot client, registers all feature handlers, and starts polling.
"""

import logging

from pyrogram import Client
from pyrogram.enums import ParseMode

from bot.config import BOT_TOKEN, API_ID, API_HASH
from bot.handlers.start import register_start
from bot.handlers.add_account import register_add_account
from bot.handlers.list_accounts import register_list_accounts
from bot.handlers.delete_account import register_delete_account
from bot.handlers.update_stars import register_update_stars
from bot.handlers.backup import register_backup


def main() -> None:
    # Basic logging so bot activity and errors are visible in the console
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )
    logging.getLogger("pyrogram").setLevel(logging.WARNING)

    # The bot itself runs in memory — the token re-authenticates on every start
    app = Client(
        "star_sender",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,
    )

    # All user-facing texts use HTML tags (<b>, <code>, <i>) — enforce globally
    app.set_parse_mode(ParseMode.HTML)

    # Register all feature handlers
    register_start(app)
    register_add_account(app)
    register_list_accounts(app)
    register_delete_account(app)
    register_update_stars(app)
    register_backup(app)

    logging.info("Star Sender bot started")
    app.run()


if __name__ == "__main__":
    main()
