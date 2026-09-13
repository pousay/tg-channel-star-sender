"""
add_account.py — Handles the Add Account flow.

Flow:
  1. Admin taps "Add Account" → bot asks for phone number.
  2. Admin sends phone → bot initiates Telegram login (sends code to device).
  3. Admin sends the login code → bot verifies it.
  4. If 2FA is required → bot asks for the TFA password and verifies it.
  5. On success → fetch star balance, save account to JSON, confirm.
"""

import os

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PasswordHashInvalid,
    FloodWait,
)

from bot.config import API_ID, API_HASH, SESSIONS_DIR
from bot.utils.auth import admin_only
from bot.utils.db import upsert_account
from bot.utils.ui import esc, safe_edit, MAIN_MENU_TEXT, main_menu_keyboard, main_menu_button


# In-memory state per admin user: tracks where in the flow they are.
# Structure: { user_id: { "step": str, "phone": str, "phone_code_hash": str,
#                         "user_client": Client, "tfa_password": None } }
_state: dict[int, dict] = {}


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_flow")]
    ])


async def _fetch_star_balance(user_client: Client) -> int:
    """Fetch the Telegram Stars balance for the authenticated user client."""
    try:
        # get_stars_balance() returns a float (amount + nanos / 1e9)
        return int(await user_client.get_stars_balance())
    except Exception:
        return 0


def register_add_account(app: Client) -> None:
    """Register all handlers related to the Add Account flow."""

    # ── Step 0: Admin taps "Add Account" button ──────────────────────────────

    @app.on_callback_query(filters.regex("^add_account$"))
    @admin_only
    async def cb_add_account(client: Client, query: CallbackQuery) -> None:
        await query.answer()
        uid = query.from_user.id
        _state[uid] = {"step": "awaiting_phone"}
        await safe_edit(
            query.message,
            "📱 لطفاً شماره تلفن اکانت مورد نظر را بفرستید:\n"
            "<code>+989123456789</code>",
            _cancel_keyboard(),
        )

    # ── Incoming text messages — routed by current step ───────────────────────

    @app.on_message(filters.text & filters.private & ~filters.command(["start", "backup"]))
    @admin_only
    async def text_router(client: Client, message: Message) -> None:
        uid = message.from_user.id
        state = _state.get(uid)
        if not state:
            return  # Not in any flow

        step = state.get("step")

        if step == "awaiting_phone":
            await _handle_phone(client, message, state, uid)
        elif step == "awaiting_code":
            await _handle_code(client, message, state, uid)
        elif step == "awaiting_tfa":
            await _handle_tfa(client, message, state, uid)

    # ── Cancel flow ───────────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex("^cancel_flow$"))
    @admin_only
    async def cb_cancel(client: Client, query: CallbackQuery) -> None:
        await query.answer()
        uid = query.from_user.id
        state = _state.pop(uid, None)
        # Disconnect the temporary user client if it exists
        if state and state.get("user_client"):
            try:
                await state["user_client"].disconnect()
            except Exception:
                pass
        await safe_edit(query.message, "❌ عملیات لغو شد.", main_menu_button())

    # ── Main menu callback ────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex("^main_menu$"))
    @admin_only
    async def cb_main_menu(client: Client, query: CallbackQuery) -> None:
        await query.answer()
        await safe_edit(query.message, MAIN_MENU_TEXT, main_menu_keyboard())


# ── Internal step handlers ────────────────────────────────────────────────────

async def _handle_phone(
    client: Client, message: Message, state: dict, uid: int
) -> None:
    """Step 1 — receive phone number and send login code."""
    phone = message.text.strip()

    # Basic validation: must start with + and contain only digits after
    if not (phone.startswith("+") and phone[1:].isdigit() and len(phone) > 7):
        await message.reply_text(
            "⚠️ فرمت شماره تلفن اشتباه است!\n"
            "لطفاً به این شکل بفرستید: <code>+989123456789</code>",
            reply_markup=_cancel_keyboard(),
        )
        return

    state["phone"] = phone

    # Create a temporary Pyrogram client for this user account
    session_name = os.path.join(SESSIONS_DIR, phone.replace("+", ""))
    os.makedirs(SESSIONS_DIR, exist_ok=True)

    user_client = Client(
        session_name,
        api_id=API_ID,
        api_hash=API_HASH,
    )

    try:
        await user_client.connect()
        sent = await user_client.send_code(phone)
        state["phone_code_hash"] = sent.phone_code_hash
        state["user_client"] = user_client
        state["step"] = "awaiting_code"

        await message.reply_text(
            f"✅ کد ورود به شماره <code>{phone}</code> ارسال شد.\n\n"
            "لطفاً کدی که در تلگرام دریافت کرده‌اید را بفرستید:\n"
            "<i>بدون فاصله، مثل <code>12345</code></i>",
            reply_markup=_cancel_keyboard(),
        )
    except FloodWait as e:
        await user_client.disconnect()
        _state.pop(uid, None)
        await message.reply_text(
            f"⏳ محدودیت موقت تلگرام!\n"
            f"لطفاً <b>{e.value}</b> ثانیه دیگر دوباره تلاش کنید.",
            reply_markup=_cancel_keyboard(),
        )
    except Exception as e:
        await user_client.disconnect()
        _state.pop(uid, None)
        await message.reply_text(
            f"❌ ارسال کد ورود ناموفق بود:\n<code>{esc(e)}</code>",
            reply_markup=_cancel_keyboard(),
        )


async def _handle_code(
    client: Client, message: Message, state: dict, uid: int
) -> None:
    """Step 2 — receive the login code and sign in."""
    code = message.text.strip()
    phone = state["phone"]
    phone_code_hash = state["phone_code_hash"]
    user_client: Client = state["user_client"]

    try:
        await user_client.sign_in(phone, phone_code_hash, code)

        # Login successful — fetch details and save
        await _finalize_login(client, message, state, uid, user_client, tfa_password=None)

    except SessionPasswordNeeded:
        # 2FA is enabled on this account
        state["step"] = "awaiting_tfa"
        await message.reply_text(
            "🔐 این اکانت تایید دو مرحله‌ای (2FA) دارد.\n"
            "لطفاً رمز دو مرحله‌ای را بفرستید:",
            reply_markup=_cancel_keyboard(),
        )
    except PhoneCodeExpired:
        await message.reply_text(
            "❌ این کد <b>منقضی</b> شده است.\nلطفاً از منوی اصلی دوباره شروع کنید.",
            reply_markup=main_menu_button(),
        )
        await user_client.disconnect()
        _state.pop(uid, None)
    except PhoneCodeInvalid:
        await message.reply_text(
            "❌ کد وارد شده <b>نامعتبر</b> است.\nلطفاً از منوی اصلی دوباره شروع کنید.",
            reply_markup=main_menu_button(),
        )
        await user_client.disconnect()
        _state.pop(uid, None)
    except Exception as e:
        await message.reply_text(
            f"❌ ورود ناموفق بود:\n<code>{esc(e)}</code>",
            reply_markup=main_menu_button(),
        )
        await user_client.disconnect()
        _state.pop(uid, None)


async def _handle_tfa(
    client: Client, message: Message, state: dict, uid: int
) -> None:
    """Step 3 — receive TFA password and complete login."""
    tfa_password = message.text.strip()
    user_client: Client = state["user_client"]

    try:
        await user_client.check_password(tfa_password)
        await _finalize_login(client, message, state, uid, user_client, tfa_password)
    except PasswordHashInvalid:
        await message.reply_text(
            "❌ رمز دو مرحله‌ای اشتباه است.\nلطفاً دوباره تلاش کنید:",
            reply_markup=_cancel_keyboard(),
        )
    except Exception as e:
        await message.reply_text(
            f"❌ تایید رمز دو مرحله‌ای ناموفق بود:\n<code>{esc(e)}</code>",
            reply_markup=main_menu_button(),
        )
        await user_client.disconnect()
        _state.pop(uid, None)


async def _finalize_login(
    client: Client,
    message: Message,
    state: dict,
    uid: int,
    user_client: Client,
    tfa_password: str | None,
) -> None:
    """After successful authentication: fetch info, save account, and confirm."""
    phone = state["phone"]

    # Get account name
    me = await user_client.get_me()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or phone

    # Get star balance
    star_balance = await _fetch_star_balance(user_client)

    # Export session string for future re-use
    session_string = await user_client.export_session_string()

    # Disconnect the temporary client (we'll reconnect via session string later)
    await user_client.disconnect()

    # Persist to database
    account = {
        "phone": phone,
        "name": name,
        "star_balance": star_balance,
        "session_string": session_string,
        "tfa_password": tfa_password,
    }
    upsert_account(account)

    # Clean up state
    _state.pop(uid, None)

    await message.reply_text(
        f"✅ <b>اکانت با موفقیت اضافه شد!</b>\n\n"
        f"👤 نام: <b>{esc(name)}</b>\n"
        f"📱 شماره: <code>{phone}</code>\n"
        f"⭐ موجودی ستاره: <b>{star_balance}</b>",
        reply_markup=main_menu_button(),
    )
