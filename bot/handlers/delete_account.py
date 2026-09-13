"""
delete_account.py — Handles the Delete Account flow.

Flow:
  1. Admin taps "Delete Account" → bot asks for the phone number.
  2. Bot validates the phone format and checks the JSON database.
  3. If found → asks for approval: "Are you sure?" with Yes / Cancel buttons.
  4. On "Yes" → the account is removed from the JSON file and confirmed.
"""

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from bot.utils.auth import admin_only
from bot.utils.db import get_account, delete_account


# In-memory state per admin user for the delete flow
_state: dict[int, dict] = {}


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")]
    ])


def _confirm_keyboard(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"delete_yes:{phone}"),
            InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel"),
        ]
    ])


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ])


def register_delete_account(app: Client) -> None:
    """Register all handlers related to the Delete Account flow."""

    # ── Step 0: Admin taps "Delete Account" ──────────────────────────────────

    @app.on_callback_query(filters.regex("^delete_account_start$"))
    @admin_only
    async def cb_delete_start(client: Client, query: CallbackQuery) -> None:
        uid = query.from_user.id
        _state[uid] = {"step": "awaiting_delete_phone"}
        await query.message.edit_text(
            "🗑 Please send the **phone number** of the account to delete.\n"
            "Format: `+1234567890`",
            reply_markup=_cancel_keyboard(),
        )

    # ── Incoming text — routed by current step ────────────────────────────────

    @app.on_message(filters.text & filters.private & ~filters.command(["start", "backup"]))
    @admin_only
    async def text_router(client: Client, message: Message) -> None:
        uid = message.from_user.id
        state = _state.get(uid)
        if not state:
            return  # Not in the delete flow

        if state.get("step") == "awaiting_delete_phone":
            await _handle_delete_phone(client, message, state, uid)

    # ── Approval: Yes → delete the account ────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^delete_yes:(.+)$"))
    @admin_only
    async def cb_delete_yes(client: Client, query: CallbackQuery) -> None:
        phone = query.data.split(":", 1)[1]
        _state.pop(query.from_user.id, None)

        if delete_account(phone):
            await query.message.edit_text(
                f"✅ Account <code>{phone}</code> has been **deleted**.",
                reply_markup=_main_menu_keyboard(),
            )
        else:
            await query.message.edit_text(
                f"⚠️ Account <code>{phone}</code> was not found.",
                reply_markup=_main_menu_keyboard(),
            )

    # ── Cancel → back to main menu ────────────────────────────────────────────

    @app.on_callback_query(filters.regex("^delete_cancel$"))
    @admin_only
    async def cb_delete_cancel(client: Client, query: CallbackQuery) -> None:
        _state.pop(query.from_user.id, None)
        await query.message.edit_text(
            "❌ Delete cancelled.",
            reply_markup=_main_menu_keyboard(),
        )


async def _handle_delete_phone(
    client: Client, message: Message, state: dict, uid: int
) -> None:
    """Validate the phone number and ask for confirmation if the account exists."""
    phone = message.text.strip()

    # 1) Format validation: must start with "+" followed by at least 7 digits
    if not (phone.startswith("+") and phone[1:].isdigit() and len(phone) > 7):
        await message.reply_text(
            "⚠️ Invalid phone number format. Please use `+1234567890`.",
            reply_markup=_cancel_keyboard(),
        )
        return

    # 2) Existence validation against the JSON database
    account = get_account(phone)
    if not account:
        _state.pop(uid, None)
        await message.reply_text(
            f"⚠️ No account found with phone <code>{phone}</code>.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    # 3) Found → ask for approval
    name = account.get("name", "Unknown")
    stars = account.get("star_balance", 0)
    await message.reply_text(
        f"⚠️ **Are you sure you want to delete this account?**\n\n"
        f"👤 Name: **{name}**\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"⭐ Stars: {stars}",
        reply_markup=_confirm_keyboard(phone),
    )
