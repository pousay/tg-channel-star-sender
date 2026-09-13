"""
backup.py — Secret /backup command.

Sends the JSON accounts database file directly to the admin as a document.
"""

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.config import DB_PATH
from bot.utils.auth import admin_only
from bot.utils.db import load_accounts


def register_backup(app: Client) -> None:
    """Register the /backup command handler."""

    @app.on_message(filters.command("backup") & filters.private)
    @admin_only
    async def backup_handler(client: Client, message: Message) -> None:
        # Ensure the DB file exists (and is valid) before sending it
        load_accounts()

        await message.reply_document(
            DB_PATH,
            caption="📦 Accounts database backup",
        )
