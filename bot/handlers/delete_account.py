"""
delete_account.py — Handles the Delete Account flow.

Flow:
  1. Admin taps "Delete Account" → bot asks for the phone number.
  2. Bot validates the phone format and checks the JSON database.
  3. If found → asks for approval: "Are you sure?" with Yes / Cancel buttons.
  4. On "Yes" → the account is removed from the JSON file and confirmed.
"""

from pyrogram import Client, ContinuePropagation, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.utils.auth import admin_only
from bot.utils.db import get_account, delete_account
from bot.utils.ui import esc, safe_edit, main_menu_button


# In-memory state per admin user for the delete flow
_state: dict[int, dict] = {}


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ انصراف", callback_data="delete_cancel")]
    ])


def _confirm_keyboard(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله، حذف شود", callback_data=f"delete_yes:{phone}"),
            InlineKeyboardButton("❌ انصراف", callback_data="delete_cancel"),
        ]
    ])


def register_delete_account(app: Client) -> None:
    """Register all handlers related to the Delete Account flow."""

    # ── Step 0: Admin taps "Delete Account" ──────────────────────────────────

    @app.on_callback_query(filters.regex("^delete_account_start$"))
    @admin_only
    async def cb_delete_start(client: Client, query: CallbackQuery) -> None:
        await query.answer()
        uid = query.from_user.id
        _state[uid] = {"step": "awaiting_delete_phone"}
        await safe_edit(
            query.message,
            "🗑 لطفاً شماره تلفن اکانت مورد نظر برای حذف را بفرستید:\n"
            "<code>+989123456789</code>",
            _cancel_keyboard(),
        )

    # ── Incoming text — routed by current step ────────────────────────────────

    @app.on_message(filters.text & filters.private & ~filters.command(["start", "backup"]))
    @admin_only
    async def text_router(client: Client, message: Message) -> None:
        uid = message.from_user.id
        state = _state.get(uid)
        if not state:
            # Not in the delete flow — let other text routers in this group
            # handle the update (dispatcher stops at the first handler that
            # returns normally).
            raise ContinuePropagation

        if state.get("step") == "awaiting_delete_phone":
            await _handle_delete_phone(client, message, state, uid)

    # ── Approval: Yes → delete the account ────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^delete_yes:(.+)$"))
    @admin_only
    async def cb_delete_yes(client: Client, query: CallbackQuery) -> None:
        await query.answer()
        phone = query.data.split(":", 1)[1]
        _state.pop(query.from_user.id, None)

        if delete_account(phone):
            await safe_edit(
                query.message,
                f"✅ اکانت <code>{phone}</code> با موفقیت حذف شد.",
                main_menu_button(),
            )
        else:
            await safe_edit(
                query.message,
                f"⚠️ اکانت <code>{phone}</code> پیدا نشد.",
                main_menu_button(),
            )

    # ── Cancel → back to main menu ────────────────────────────────────────────

    @app.on_callback_query(filters.regex("^delete_cancel$"))
    @admin_only
    async def cb_delete_cancel(client: Client, query: CallbackQuery) -> None:
        await query.answer()
        _state.pop(query.from_user.id, None)
        await safe_edit(
            query.message,
            "❌ عملیات حذف لغو شد.",
            main_menu_button(),
        )


async def _handle_delete_phone(
    client: Client, message: Message, state: dict, uid: int
) -> None:
    """Validate the phone number and ask for confirmation if the account exists."""
    phone = message.text.strip()

    # 1) Format validation: must start with "+" followed by at least 7 digits
    if not (phone.startswith("+") and phone[1:].isdigit() and len(phone) > 7):
        await message.reply_text(
            "⚠️ فرمت شماره تلفن اشتباه است!\n"
            "لطفاً به این شکل بفرستید: <code>+989123456789</code>",
            reply_markup=_cancel_keyboard(),
        )
        return

    # 2) Existence validation against the JSON database
    account = get_account(phone)
    if not account:
        _state.pop(uid, None)
        await message.reply_text(
            f"⚠️ اکانتی با شماره <code>{phone}</code> پیدا نشد.",
            reply_markup=main_menu_button(),
        )
        return

    # 3) Found → ask for approval
    name = account.get("name", "نامشخص")
    stars = account.get("star_balance", 0)
    await message.reply_text(
        f"⚠️ <b>آیا از حذف این اکانت مطمئن هستید؟</b>\n\n"
        f"👤 نام: <b>{esc(name)}</b>\n"
        f"📱 شماره: <code>{phone}</code>\n"
        f"⭐ ستاره: {stars}",
        reply_markup=_confirm_keyboard(phone),
    )
