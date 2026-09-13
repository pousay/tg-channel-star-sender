"""
start.py — Handles the /start command and shows the main menu.
"""

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.utils.auth import admin_only


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build and return the main menu inline keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Account", callback_data="add_account")],
        [InlineKeyboardButton("📋 List Accounts", callback_data="list_accounts:0")],
        [InlineKeyboardButton("⭐ Update Stars Balance", callback_data="update_stars")],
    ])


def register_start(app: Client) -> None:
    """Register the /start command handler."""

    @app.on_message(filters.command("start") & filters.private)
    @admin_only
    async def start_handler(client: Client, message: Message) -> None:
        """Send the main menu when /start is received."""
        await message.reply_text(
            "👋 **Star Sender — Account Manager**\n\nChoose an action:",
            reply_markup=main_menu_keyboard(),
        )
