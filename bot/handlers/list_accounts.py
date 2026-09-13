"""
list_accounts.py — Handles the List Accounts flow with pagination.

Shows 10 accounts per page. Each page shows:
  - Phone number (copyable via <code> tags)
  - Name
  - Star balance

Navigation: Previous / Next buttons + a page indicator.
Also shows a Delete Account button and a Back button.
"""

import math

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.utils.auth import admin_only
from bot.utils.db import load_accounts
from bot.utils.ui import esc, safe_edit

PAGE_SIZE = 10


def _build_accounts_page(accounts: list[dict], page: int) -> tuple[str, InlineKeyboardMarkup]:
    """
    Build the message text and keyboard for a given page of accounts.

    Works correctly for an empty list too (shows a friendly notice).
    Returns (text, keyboard).
    """
    total = len(accounts)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))  # Clamp

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_accounts = accounts[start:end]

    if not page_accounts:
        text = "📭 هنوز هیچ اکانتی ذخیره نشده است."
    else:
        lines = [f"📋 <b>لیست اکانت‌ها</b>\n📄 صفحه {page + 1} از {total_pages}\n"]
        for i, acc in enumerate(page_accounts, start=start + 1):
            phone = acc.get("phone", "N/A")
            name = acc.get("name", "نامشخص")
            stars = acc.get("star_balance", 0)
            lines.append(
                f"{i}. 👤 <b>{esc(name)}</b>\n"
                f"   📱 <code>{phone}</code>\n"
                f"   ⭐ ستاره: {stars}"
            )
        text = "\n\n".join(lines)

    # Build navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton("◀️ قبلی", callback_data=f"list_accounts:{page - 1}")
        )
    nav_buttons.append(
        InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop")
    )
    if (page + 1) * PAGE_SIZE < total:
        nav_buttons.append(
            InlineKeyboardButton("بعدی ▶️", callback_data=f"list_accounts:{page + 1}")
        )

    keyboard = InlineKeyboardMarkup([
        nav_buttons,
        [InlineKeyboardButton("🗑 حذف اکانت", callback_data="delete_account_start")],
        [InlineKeyboardButton("🏠 بازگشت", callback_data="main_menu")],
    ])

    return text, keyboard


def register_list_accounts(app: Client) -> None:
    """Register all handlers for the List Accounts flow."""

    @app.on_callback_query(filters.regex(r"^list_accounts:(\d+)$"))
    @admin_only
    async def cb_list_accounts(client: Client, query: CallbackQuery) -> None:
        """Show the accounts list at the requested page."""
        # Always answer the callback so the client never shows a button error
        await query.answer()

        page = int(query.data.split(":")[1])
        accounts = load_accounts()
        text, keyboard = _build_accounts_page(accounts, page)

        await safe_edit(query.message, text, keyboard)

    @app.on_callback_query(filters.regex("^noop$"))
    @admin_only
    async def cb_noop(client: Client, query: CallbackQuery) -> None:
        """No-operation callback for the page indicator button."""
        await query.answer()
