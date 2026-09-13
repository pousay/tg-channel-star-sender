"""
start.py — Handles the /start command and shows the main menu.
"""

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.utils.auth import admin_only
from bot.utils.ui import MAIN_MENU_TEXT, main_menu_keyboard


def register_start(app: Client) -> None:
    """Register the /start command handler."""

    @app.on_message(filters.command("start") & filters.private)
    @admin_only
    async def start_handler(client: Client, message: Message) -> None:
        """Send the main menu when /start is received."""
        await message.reply_text(
            MAIN_MENU_TEXT,
            reply_markup=main_menu_keyboard(),
        )
