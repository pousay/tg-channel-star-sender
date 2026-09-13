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
from pyrogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
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


# In-memory state per admin user: tracks where in the flow they are.
# Structure: { user_id: { "step": str, "phone": str, "phone_code_hash": str,
#                         "user_client": Client, "tfa_password": None } }
_state: dict[int, dict] = {}


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_flow")]
    ])


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ])


async def _fetch_star_balance(user_client: Client) -> int:
    """Fetch the Telegram Stars balance for the authenticated user client."""
    try:
        balance = await user_client.get_stars_balance()
        # get_stars_balance returns a StarsStatus object; extract the amount
        return balance.amount if hasattr(balance, "amount") else int(balance)
    except Exception:
        return 0


def register_add_account(app: Client) -> None:
    """Register all handlers related to the Add Account flow."""

    # ── Step 0: Admin taps "Add Account" button ──────────────────────────────

    @app.on_callback_query(filters.regex("^add_account$"))
    @admin_only
    async def cb_add_account(client: Client, query: CallbackQuery) -> None:
        uid = query.from_user.id
        _state[uid] = {"step": "awaiting_phone"}
        await query.message.edit_text(
            "📱 Please send the **phone number** of the account to add.\n"
            "Format: `+1234567890`",
            reply_markup=_cancel_keyboard(),
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
        uid = query.from_user.id
        state = _state.pop(uid, None)
        # Disconnect the temporary user client if it exists
        if state and state.get("user_client"):
            try:
                await state["user_client"].disconnect()
            except Exception:
                pass
        await query.message.edit_text(
            "❌ Flow cancelled.",
            reply_markup=_main_menu_keyboard(),
        )

    # ── Main menu callback ────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex("^main_menu$"))
    @admin_only
    async def cb_main_menu(client: Client, query: CallbackQuery) -> None:
        from bot.handlers.start import main_menu_keyboard
        await query.message.edit_text(
            "👋 **Star Sender — Account Manager**\n\nChoose an action:",
            reply_markup=main_menu_keyboard(),
        )


# ── Internal step handlers ────────────────────────────────────────────────────

async def _handle_phone(
    client: Client, message: Message, state: dict, uid: int
) -> None:
    """Step 1 — receive phone number and send login code."""
    phone = message.text.strip()

    # Basic validation: must start with + and contain only digits after
    if not (phone.startswith("+") and phone[1:].isdigit() and len(phone) > 7):
        await message.reply_text(
            "⚠️ Invalid phone number format. Please use `+1234567890`.",
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
            f"✅ Login code sent to **{phone}**.\n\n"
            "Please send the **login code** you received on Telegram.\n"
            "_(Enter it without spaces, e.g. `12345`)_",
            reply_markup=_cancel_keyboard(),
        )
    except FloodWait as e:
        await user_client.disconnect()
        _state.pop(uid, None)
        await message.reply_text(
            f"⏳ Flood wait: please try again in **{e.value} seconds**.",
            reply_markup=_cancel_keyboard(),
        )
    except Exception as e:
        await user_client.disconnect()
        _state.pop(uid, None)
        await message.reply_text(
            f"❌ Failed to send login code: `{e}`",
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
        signed_in = await user_client.sign_in(phone, phone_code_hash, code)

        # Login successful — fetch details and save
        await _finalize_login(client, message, state, uid, user_client, tfa_password=None)

    except SessionPasswordNeeded:
        # 2FA is enabled on this account
        state["step"] = "awaiting_tfa"
        await message.reply_text(
            "🔐 This account has **Two-Factor Authentication** enabled.\n"
            "Please send the **TFA password**.",
            reply_markup=_cancel_keyboard(),
        )
    except (PhoneCodeInvalid, PhoneCodeExpired) as e:
        error = "expired" if isinstance(e, PhoneCodeExpired) else "invalid"
        await message.reply_text(
            f"❌ The code is **{error}**. Please try again from /start.",
            reply_markup=_main_menu_keyboard(),
        )
        await user_client.disconnect()
        _state.pop(uid, None)
    except Exception as e:
        await message.reply_text(
            f"❌ Login failed: `{e}`",
            reply_markup=_main_menu_keyboard(),
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
            "❌ Incorrect TFA password. Please try again.",
            reply_markup=_cancel_keyboard(),
        )
    except Exception as e:
        await message.reply_text(
            f"❌ TFA verification failed: `{e}`",
            reply_markup=_main_menu_keyboard(),
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
        f"✅ **Account added successfully!**\n\n"
        f"👤 Name: **{name}**\n"
        f"📱 Phone: `{phone}`\n"
        f"⭐ Stars: **{star_balance}**",
        reply_markup=_main_menu_keyboard(),
    )
