"""
ui.py — Shared UI helpers: common texts, keyboards, and safe editing.

All user-facing texts are in Persian (Farsi) and use HTML parse mode.
"""

import html as html_lib

from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


# ── Shared texts ──────────────────────────────────────────────────────────────

MAIN_MENU_TEXT = (
    "👋 <b>استار سندر</b>\n\n"
    "پنل مدیریت اکانت‌ها — لطفاً یک گزینه را انتخاب کنید:"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def esc(value) -> str:
    """Escape a dynamic value so it is safe to embed in HTML parse mode."""
    return html_lib.escape(str(value))


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """The main menu inline keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن اکانت", callback_data="add_account")],
        [InlineKeyboardButton("📋 لیست اکانت‌ها", callback_data="list_accounts:0")],
        [InlineKeyboardButton("⭐ به‌روزرسانی موجودی ستاره", callback_data="update_stars")],
    ])


def main_menu_button() -> InlineKeyboardMarkup:
    """A single-row keyboard with a 'back to main menu' button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")]
    ])


async def safe_edit(message, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    """
    Edit a message with HTML parse mode, tolerating 'message not modified'
    (raised by Telegram when the new content is identical to the current one).
    Returns the edited Message, or the original one if nothing changed.
    """
    try:
        return await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="html",
        )
    except MessageNotModified:
        return message
