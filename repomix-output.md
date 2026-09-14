This file is a merged representation of the entire codebase, combined into a single document by Repomix.

# File Summary

## Purpose
This file contains a packed representation of the entire repository's contents.
It is designed to be easily consumable by AI systems for analysis, code review,
or other automated processes.

## File Format
The content is organized as follows:
1. This summary section
2. Repository information
3. Directory structure
4. Repository files (if enabled)
5. Multiple file entries, each consisting of:
  a. A header with the file path (## File: path/to/file)
  b. The full contents of the file in a code block

## Usage Guidelines
- This file should be treated as read-only. Any changes should be made to the
  original repository files, not this packed version.
- When processing this file, use the file path to distinguish
  between different files in the repository.
- Be aware that this file may contain sensitive information. Handle it with
  the same level of security as you would the original repository.

## Notes
- Some files may have been excluded based on .gitignore rules and Repomix's configuration
- Binary files are not included in this packed representation. Please refer to the Repository Structure section for a complete list of file paths, including binary files
- Files matching patterns in .gitignore are excluded
- Files matching default ignore patterns are excluded
- Files are sorted by Git change count (files with more changes are at the bottom)

# Directory Structure
```
handlers/
  __init__.py
  add_account.py
  backup.py
  channel_monitor.py
  delete_account.py
  list_accounts.py
  start.py
  update_stars.py
utils/
  __init__.py
  auth.py
  db.py
  notify.py
  post_store.py
  ui.py
__init__.py
config.py
```

# Files

## File: handlers/__init__.py
```python

```

## File: handlers/add_account.py
```python
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

from pyrogram import Client, ContinuePropagation, filters
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
            # Not in the add-account flow — let other text routers in this
            # group handle the update (dispatcher stops at the first handler
            # that returns normally).
            raise ContinuePropagation

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
```

## File: handlers/backup.py
```python
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
            caption="📦 فایل بکاپ اکانت‌ها",
        )
```

## File: handlers/channel_monitor.py
```python
"""
channel_monitor.py — Interval-based reactions + Star gifting to channel posts.

The target channel may be private (numeric ID), where live on_message
detection is unreliable — so detection is done by POLLING instead:

  Every POLL_INTERVAL_MINUTES, one of the stored user accounts (accounts
  must be channel members anyway to react/paid-react there; get_chat_history
  is a users-only MTProto method) fetches the last POLL_FETCH_COUNT posts:

  1. Deduplicate: every keyword post gets one record in the processed-posts
     store (bot/utils/post_store.py) — repeated cycles never re-process it.
  2. Filter: the post must contain POST_KEYWORD in its text or caption.
  3. Delay: act when the post is at least DELAY_MINUTES old (by post date).
     A keyword post first seen older than POST_MAX_AGE_MINUTES is skipped
     as stale (protects against acting on long-gone giveaways).
  4. Pick a random number of accounts between MIN_ACCOUNTS and MAX_ACCOUNTS.
  5. Validate: every selected account must have balance >= MAX_STARS.
     Not enough eligible accounts → alert the admins, send nothing.
  6. Assign a random reaction (from REACTIONS) to each account.
  7. Assign a random Star amount (MIN_STARS..MAX_STARS) to each account.
  8. Per account: connect via saved session, send the reaction, then the
     Stars (paid reaction). Errors are handled per account.
  9. Logging — every critical and half-critical event goes to ALL admins
     via bot/utils/notify.py:
       🚨 critical:  poll cycle crash, no readable poller account,
                     post-processing crash
       ⚠️ half-critical: per-account failures, not-enough-accounts alert
       ℹ️ info:      per-account successes, per-post summary
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from pyrogram import Client
from pyrogram.types import Message

from bot.config import (
    DELAY_MINUTES,
    MAX_ACCOUNTS,
    MAX_STARS,
    MIN_ACCOUNTS,
    MIN_STARS,
    POLL_FETCH_COUNT,
    POLL_INTERVAL_MINUTES,
    POST_MAX_AGE_MINUTES,
    POST_KEYWORD,
    REACTIONS,
    TARGET_CHANNEL,
)
from bot.utils import post_store
from bot.utils.db import load_accounts
from bot.utils.notify import notify_admin
from bot.utils.ui import esc

# Persistent poller: a user account that can read the target channel.
# Kept connected across cycles; rotated to the next account on failure.
_poller: Client | None = None

# True while we are in the "no account can read the channel" state —
# used to notify the admins once instead of spamming every cycle.
_no_poller_notified: bool = False

# Small pause between accounts to reduce flood risk
_PER_ACCOUNT_PAUSE_SECONDS = 1.5


# ── Pure helpers (unit-testable, no Telegram I/O) ─────────────────────────────


def _post_link(message: Message) -> str:
    """Build a clickable link for the post (works for private channels too)."""
    chat = message.chat
    if chat is not None and chat.username:
        return f"https://t.me/{chat.username}/{message.id}"
    # Private superchannel: internal link format t.me/c/<raw_id>/<msg_id>
    if chat is not None and str(chat.id).startswith("-100"):
        return f"https://t.me/c/{str(chat.id)[4:]}/{message.id}"
    return f"پست #{message.id} در {TARGET_CHANNEL}"


def _contains_keyword(message: Message) -> bool:
    """True if POST_KEYWORD appears (case-insensitive) in the text or caption."""
    keyword = POST_KEYWORD.lower()
    text = message.text or ""
    caption = message.caption or ""
    return keyword in text.lower() or keyword in caption.lower()


def _post_key(message: Message) -> str:
    """Stable dedupe key: albums share one media_group_id, count them once."""
    chat_id = message.chat.id if message.chat else 0
    return f"{chat_id}:{message.media_group_id or message.id}"


def _decide_action(
    entry: dict | None, message: Message, now: datetime
) -> tuple[str, dict | None]:
    """
    Pure decision for one fetched post (no I/O — unit-testable).

    Returns (action, record):
      "act"   — run the pipeline now; `record` is set only for freshly
                discovered posts (the caller must persist it as processing)
      "wait"  — not yet due; `record` is set only for freshly discovered
                posts (persisted as pending)
      "skip"  — too old; `record` persisted as skipped
    `entry` is the existing store record (or None if never seen). Callers
    must pre-filter finished/processing entries before calling this.
    """
    post_date = message.date or now
    if post_date.tzinfo is None:
        post_date = post_date.replace(tzinfo=timezone.utc)

    if entry is None:
        # First time we see this keyword post
        if now - post_date > timedelta(minutes=POST_MAX_AGE_MINUTES):
            return "skip", {"status": "skipped", "reason": "too_old"}
        act_at = post_date + timedelta(minutes=DELAY_MINUTES)
        record = {
            "status": "pending",
            "post_date": post_date.isoformat(),
            "act_at": act_at.isoformat(),
        }
        if act_at <= now:
            record["status"] = "processing"
            return "act", record
        return "wait", record

    # Already pending: act once act_at has passed
    if entry.get("status") == "pending":
        act_at = datetime.fromisoformat(entry["act_at"])
        if act_at <= now:
            return "act", None
    return "wait", None


def _select_accounts(count: int) -> tuple[list[dict], int]:
    """
    Randomly select `count` eligible accounts (balance >= MAX_STARS).

    Returns (selected, available):
      - selected: list of accounts (shorter than `count` if not enough)
      - available: how many eligible accounts existed in the pool
    """
    accounts = load_accounts()
    eligible = [
        acc for acc in accounts if int(acc.get("star_balance", 0) or 0) >= MAX_STARS
    ]
    if len(eligible) < count:
        return [], len(eligible)
    return random.sample(eligible, count), len(eligible)


def _now() -> str:
    """Human-readable timestamp for the log lines."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _failure_reason(e: Exception) -> str:
    """Translate a per-account exception into a short Persian reason."""
    from pyrogram.errors import (
        AuthKeyUnregistered,
        FloodWait,
        RPCError,
        Unauthorized,
        UserDeactivated,
    )

    if isinstance(e, FloodWait):
        return f"محدودیت موقت تلگرام ({e.value} ثانیه)"
    if isinstance(e, (AuthKeyUnregistered, Unauthorized)):
        return "سشن باطل شده یا از اکانت خارج شده است"
    if isinstance(e, UserDeactivated):
        return "اکانت غیرفعال (حذف) شده است"
    if isinstance(e, RPCError):
        return f"خطای تلگرام: {esc(e)}"
    return f"{type(e).__name__}: {esc(e)}"


# ── Poller account management ─────────────────────────────────────────────────


async def _acquire_poller(client: Client) -> Client | None:
    """
    Reuse the persistent poller, or connect a user account that can read
    the target channel. Tries accounts in stored order; keeps the working
    one connected across cycles and rotates to the next on failure.
    """
    global _poller, _no_poller_notified

    if _poller is not None:
        return _poller
    n = 0
    for account in load_accounts():
        if n == 1:
            return

        session_string = account.get("session_string")
        if not session_string:
            continue

        candidate = Client("poller", session_string=session_string, in_memory=True)
        try:
            await candidate.connect()
            # Verify the account can actually read the channel history
            async for _ in candidate.get_chat_history(TARGET_CHANNEL, limit=1):
                break
            _poller = candidate
            logging.info("Poller account: %s", account.get("phone", "?"))
            if _no_poller_notified:
                # We were down before — tell the admins we recovered
                await notify_admin(
                    client,
                    "✅ <b>بررسی کانال دوباره وصل شد</b> — اکانت خواندن: "
                    f"<code>{esc(account.get('phone', '?'))}</code>",
                )
                _no_poller_notified = False
            n = 1
            return _poller

        except Exception as e:
            logging.warning(
                "Poller account %s unusable: %s", account.get("phone", "?"), e
            )
            try:
                await candidate.disconnect()
            except Exception:
                pass

    return None


# ── Per-post pipeline ─────────────────────────────────────────────────────────


async def _process_post(client: Client, message: Message, link: str) -> None:
    """Run the reaction + star-gifting pipeline for one due post."""
    count = random.randint(MIN_ACCOUNTS, MAX_ACCOUNTS)
    selected, available = _select_accounts(count)

    if len(selected) < count:
        await post_store.mark(
            _post_key(message), "skipped", reason="not_enough_eligible"
        )
        await notify_admin(
            client,
            f"⚠️ پردازش پست {link} ممکن نشد — به <b>{count}</b> اکانت واجد نیاز بود، "
            f"اما فقط <b>{available}</b> اکانت موجودی ≥ <b>{MAX_STARS}</b> ستاره داشت.\n"
            f"هیچ ستاره یا ری‌اکشنی ارسال نشد.",
        )
        return

    ok_actions = 0  # successful reactions + star sends
    fail_actions = 0  # failed reactions + star sends
    total_stars = 0  # stars actually delivered

    for account in selected:
        phone = account.get("phone", "نامشخص")
        name = esc(account.get("name", "نامشخص"))
        reaction = random.choice(REACTIONS)
        stars = random.randint(MIN_STARS, MAX_STARS)
        lines: list[str] = []

        user_client = Client(
            "actor",
            session_string=account.get("session_string"),
            in_memory=True,
        )

        try:
            await user_client.connect()
        except Exception as e:
            fail_actions += 1
            await notify_admin(
                client,
                f"❌ اکانت <code>{phone}</code> ({name}) — اتصال با سشن ناموفق بود — "
                f"دلیل: {_failure_reason(e)}\n"
                f"پست: {link}",
            )
            continue

        # Reaction first…
        try:
            await user_client.send_reaction(
                TARGET_CHANNEL, message_id=message.id, emoji=reaction
            )
            ok_actions += 1
            lines.append(
                f"✅ اکانت <code>{phone}</code> ({name}) — ری‌اکشن {reaction} ارسال شد — "
                f"پست: {link} — 🕒 {_now()}"
            )
        except Exception as e:
            fail_actions += 1
            lines.append(
                f"❌ اکانت <code>{phone}</code> ({name}) — ارسال ری‌اکشن {reaction} ناموفق — "
                f"دلیل: {_failure_reason(e)}\nپست: {link}"
            )

        # …then the Stars (paid reaction)
        try:
            await user_client.send_paid_reaction(
                TARGET_CHANNEL, message_id=message.id, amount=stars
            )
            ok_actions += 1
            total_stars += stars
            lines.append(
                f"✅ اکانت <code>{phone}</code> ({name}) — <b>{stars}</b> ستاره ارسال شد — "
                f"پست: {link} — 🕒 {_now()}"
            )
        except Exception as e:
            fail_actions += 1
            lines.append(
                f"❌ اکانت <code>{phone}</code> ({name}) — ارسال <b>{stars}</b> ستاره ناموفق — "
                f"دلیل: {_failure_reason(e)}\nپست: {link}"
            )

        await user_client.disconnect()

        # Detailed per-account log to all admins
        await notify_admin(client, "\n".join(lines))
        await asyncio.sleep(_PER_ACCOUNT_PAUSE_SECONDS)

    # Per-post summary
    await notify_admin(
        client,
        f"📊 <b>خلاصه پست</b>\n"
        f"🔗 پست: {link}\n"
        f"👥 اکانت‌های انتخاب‌شده: <b>{count}</b>\n"
        f"✅ عملیات موفق: <b>{ok_actions}</b>\n"
        f"❌ عملیات ناموفق: <b>{fail_actions}</b>\n"
        f"⭐ مجموع ستاره ارسال‌شده: <b>{total_stars}</b>",
    )


async def _handle_fetched_post(client: Client, message: Message, now: datetime) -> None:
    """Store/decision step for one fetched post (dedupe + filter + delay)."""
    key = _post_key(message)

    # Cheap keyword check runs before any store access — non-matching posts
    # are intentionally not stored (re-checking them each cycle is free).
    if not _contains_keyword(message):
        return

    entry = await post_store.get_post(key)
    if entry is not None and entry.get("status") in (
        "processing",
        "done",
        "failed",
        "skipped",
    ):
        return  # already handled — the dedupe store's whole purpose

    action, record = _decide_action(entry, message, now)

    if action == "skip":
        await post_store.ensure(key, message.date, now, status=record["status"])
        await post_store.mark(key, "skipped", reason=record.get("reason"))
        logging.info("Post %s skipped: %s", message.id, record.get("reason"))
        return

    if action == "wait":
        if record is not None:
            await post_store.ensure(
                key,
                message.date,
                datetime.fromisoformat(record["act_at"]),
            )
        return  # not due yet — next cycle will pick it up

    # action == "act"
    if entry is None:
        await post_store.ensure(
            key,
            message.date,
            datetime.fromisoformat(record["act_at"]),
            status="processing",
        )
    else:
        await post_store.mark(key, "processing")

    link = _post_link(message)
    logging.info("Post %s is due — processing (key=%s)", message.id, key)
    try:
        await _process_post(client, message, link)
        await post_store.mark(key, "done", link=link)
    except Exception as e:
        logging.exception("Post processing crashed")
        await post_store.mark(key, "failed", error=str(e))
        # 🚨 critical — the admins must know a giveaway was not completed
        await notify_admin(
            client,
            f"🚨 <b>پردازش پست با خطا متوقف شد</b>\n"
            f"🔗 پست: {link}\n"
            f"❌ خطا: <code>{esc(e)}</code>",
        )


# ── Poll cycle + loop ─────────────────────────────────────────────────────────


async def poll_once(client: Client) -> int:
    """Run one poll cycle. Returns the number of keyword posts found."""
    global _poller, _no_poller_notified

    poller = await _acquire_poller(client)
    if poller is None:
        if not _no_poller_notified:
            # 🚨 critical — nothing can be processed at all
            await notify_admin(
                client,
                "🚨 <b>هیچ اکانتی نمی‌تواند کانال را بخواند!</b>\n"
                "هیچ اکانتی در دیتابیس سشن معتبری برای خواندن "
                f"<code>{esc(str(TARGET_CHANNEL))}</code> ندارد.\n"
                "بررسی: اکانت‌ها باید عضو کانال باشند و سشن‌ها معتبر باشند.",
            )
            _no_poller_notified = True
        return 0

    messages: list[Message] = []
    try:
        async for m in poller.get_chat_history(TARGET_CHANNEL, limit=POLL_FETCH_COUNT):
            if m.service:
                continue
            messages.append(m)
    except Exception:
        # Poller died — drop it so the next cycle rotates to another account
        try:
            await poller.disconnect()
        except Exception:
            pass
        _poller = None
        raise

    now = datetime.now(timezone.utc)
    found = 0
    for message in messages:
        if _contains_keyword(message):
            found += 1
        await _handle_fetched_post(client, message, now)
    return found


async def poll_loop(client: Client) -> None:
    """Forever: poll → process → sleep POLL_INTERVAL_MINUTES."""
    logging.info(
        "Channel poller started — every %s min, last %s posts of %s",
        POLL_INTERVAL_MINUTES,
        POLL_FETCH_COUNT,
        TARGET_CHANNEL,
    )
    while True:
        try:
            await poll_once(client)
        except Exception as e:
            # 🚨 critical — a whole cycle failed
            logging.exception("Poll cycle failed")
            await notify_admin(
                client,
                f"🚨 <b>خطا در چرخه بررسی کانال</b>\n"
                f"❌ خطا: <code>{esc(e)}</code>\n"
                f"🕒 {_now()}",
            )
        await asyncio.sleep(POLL_INTERVAL_MINUTES * 60)


def register_channel_monitor(app: Client) -> None:
    """Spawn the polling loop when the client starts."""

    @app.on_start()
    async def _spawn_poller(client: Client) -> None:
        asyncio.create_task(poll_loop(client))
```

## File: handlers/delete_account.py
```python
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
```

## File: handlers/list_accounts.py
```python
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
```

## File: handlers/start.py
```python
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
```

## File: handlers/update_stars.py
```python
"""
update_stars.py — Handles the "Update Stars Balance" flow.

Walks through every saved account, connects using its saved session string,
fetches the current Telegram Stars balance, and updates the JSON database.

Accounts that cannot be accessed (revoked session, connection failure, etc.)
are reported to the admin with the reason for the failure.
"""

import asyncio

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    AuthKeyUnregistered,
    UserDeactivated,
    Unauthorized,
    FloodWait,
)

from bot.config import API_ID, API_HASH
from bot.utils.auth import admin_only
from bot.utils.db import load_accounts, upsert_account
from bot.utils.ui import esc, safe_edit, main_menu_button


def register_update_stars(app: Client) -> None:
    """Register the Update Stars Balance callback handler."""

    @app.on_callback_query(filters.regex("^update_stars$"))
    @admin_only
    async def cb_update_stars(client: Client, query: CallbackQuery) -> None:
        await query.answer()
        accounts = load_accounts()

        if not accounts:
            await query.answer("📭 هنوز هیچ اکانتی ذخیره نشده است.", show_alert=True)
            return

        total = len(accounts)

        # Initial progress message
        status = await safe_edit(
            query.message,
            f"⏳ در حال به‌روزرسانی موجودی ستاره‌ها… (0/{total})",
        )

        updated: list[tuple[str, int]] = []   # (phone, new_balance)
        failed: list[tuple[str, str]] = []    # (phone, reason)

        for i, account in enumerate(accounts, start=1):
            phone = account.get("phone", "نامشخص")
            session_string = account.get("session_string")

            if not session_string:
                failed.append((phone, "سشنی برای این اکانت ذخیره نشده است."))
                await safe_edit(status, f"⏳ در حال به‌روزرسانی موجودی ستاره‌ها… ({i}/{total})")
                continue

            user_client = Client(
                "updater",
                session_string=session_string,
                in_memory=True,
            )

            try:
                await user_client.connect()
                balance = int(await user_client.get_stars_balance())
                account["star_balance"] = balance
                upsert_account(account)
                updated.append((phone, balance))
            except FloodWait as e:
                failed.append((phone, f"محدودیت موقت تلگرام — {e.value} ثانیه دیگر تلاش کنید."))
                await asyncio.sleep(e.value)
            except AuthKeyUnregistered:
                failed.append((phone, "سشن باطل شده یا از اکانت خارج شده است."))
            except UserDeactivated:
                failed.append((phone, "اکانت غیرفعال (حذف) شده است."))
            except Unauthorized:
                failed.append((phone, "سشن دیگر معتبر نیست."))
            except Exception as e:
                failed.append((phone, f"{type(e).__name__}: {e}"))
            finally:
                try:
                    await user_client.disconnect()
                except Exception:
                    pass

                # Live progress update
                await safe_edit(status, f"⏳ در حال به‌روزرسانی موجودی ستاره‌ها… ({i}/{total})")

        # ── Build the final report ────────────────────────────────────────────
        lines = [
            f"⭐ <b>به‌روزرسانی موجودی ستاره‌ها تمام شد!</b>\n\n"
            f"✅ موفق: <b>{len(updated)}</b>\n"
            f"❌ ناموفق: <b>{len(failed)}</b>"
        ]

        if updated:
            lines.append("\n<b>✅ به‌روز شده:</b>")
            for phone, balance in updated:
                lines.append(f"✔️ <code>{esc(phone)}</code> — {balance} ستاره")

        if failed:
            lines.append("\n<b>❌ ناموفق:</b>")
            for phone, reason in failed:
                lines.append(f"✖️ <code>{esc(phone)}</code>\n└─ {esc(reason)}")

        await safe_edit(status, "\n".join(lines), main_menu_button())
```

## File: utils/__init__.py
```python

```

## File: utils/auth.py
```python
"""
auth.py — Admin-only access guard decorator.

ADMIN_IDS is the single source of truth for admin identity: it governs
both bot-panel access (here) and event-log delivery (see bot/utils/notify.py).
"""

from functools import wraps
from pyrogram.types import Message, CallbackQuery
from bot.config import ADMIN_IDS


def admin_only(func):
    """
    Decorator that silently ignores messages/callbacks from non-admin users.
    Works for both Message and CallbackQuery handlers.
    """
    @wraps(func)
    async def wrapper(client, update, *args, **kwargs):
        if isinstance(update, CallbackQuery):
            user_id = update.from_user.id
        elif isinstance(update, Message):
            user_id = update.from_user.id
        else:
            return

        if user_id not in ADMIN_IDS:
            return  # Silently ignore non-admins

        return await func(client, update, *args, **kwargs)

    return wrapper
```

## File: utils/db.py
```python
"""
db.py — JSON-based database helpers for reading and writing account records.

Schema per account:
{
    "phone": "+1234567890",
    "name": "John Doe",
    "star_balance": 0,
    "session_string": "...",
    "tfa_password": null
}
"""

import json
import os
from typing import Optional
from bot.config import DB_PATH


def _ensure_db() -> None:
    """Create the database file with an empty list if it doesn't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w") as f:
            json.dump([], f, indent=2)


def load_accounts() -> list[dict]:
    """Return all accounts from the JSON database."""
    _ensure_db()
    with open(DB_PATH, "r") as f:
        return json.load(f)


def save_accounts(accounts: list[dict]) -> None:
    """Persist the full accounts list to the JSON database."""
    _ensure_db()
    with open(DB_PATH, "w") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


def get_account(phone: str) -> Optional[dict]:
    """Find and return a single account by phone number, or None."""
    for acc in load_accounts():
        if acc["phone"] == phone:
            return acc
    return None


def upsert_account(account: dict) -> None:
    """
    Insert a new account or update an existing one (matched by phone).
    """
    accounts = load_accounts()
    for i, acc in enumerate(accounts):
        if acc["phone"] == account["phone"]:
            accounts[i] = account
            save_accounts(accounts)
            return
    accounts.append(account)
    save_accounts(accounts)


def delete_account(phone: str) -> bool:
    """
    Remove an account by phone number.
    Returns True if it was found and deleted, False otherwise.
    """
    accounts = load_accounts()
    new_accounts = [acc for acc in accounts if acc["phone"] != phone]
    if len(new_accounts) == len(accounts):
        return False
    save_accounts(new_accounts)
    return True
```

## File: utils/notify.py
```python
"""
notify.py — Helper for sending detailed event logs to the admins.

Every critical event (success, failure, or "not enough accounts") is
delivered to ALL admins listed in ADMIN_IDS — the same admins who manage
the bot panel. There is no separate log-recipient variable.

Delivery to one admin never blocks delivery to the others: a per-admin
failure is logged locally (with the admin id) and the fan-out continues.
The helper never raises to the caller, so a broken notification cannot
crash a flow mid-way — failed deliveries are still recorded in the console.
"""

import logging

from pyrogram import Client

from bot.config import ADMIN_IDS


async def notify_admin(client: Client, text: str) -> None:
    """Send a log message to every admin; never raise on failure."""
    delivered = 0
    for admin_id in ADMIN_IDS:
        try:
            await client.send_message(admin_id, text, parse_mode="html")
            delivered += 1
        except Exception as e:
            # Keep a local trace so the event is never fully lost
            logging.warning(
                "Failed to deliver admin notification to %s: %s", admin_id, e
            )
    logging.debug(
        "Admin notification delivered to %d/%d admins", delivered, len(ADMIN_IDS)
    )
```

## File: utils/post_store.py
```python
"""
post_store.py — Small JSON-backed store of already-checked channel posts.

Polling re-encounters the same posts on every cycle, so each keyword post
gets exactly one record here; the store prevents re-processing:

  status:
    pending    — seen, waiting until act_at (post date + DELAY_MINUTES)
    processing — being acted on right now (crash-safe marker)
    done       — reactions/stars pipeline finished
    skipped    — never processed (too old, or not enough eligible accounts)
    failed     — pipeline crashed (already reported to the admins)

Only keyword-matching posts are recorded — re-validating non-matching
posts is cheap (a substring check), so they are not stored and the file
stays small. Finished records are pruned after RETENTION_DAYS.

All operations are async and serialized with an asyncio.Lock so the poll
cycle and any concurrent flow can never corrupt the file.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

from bot.config import POSTS_DB_PATH

# Keep finished records for a week, then prune them
RETENTION_DAYS = 7

# Never prune records for unfinished work
_KEEP_STATUSES = ("pending", "processing")

_lock = asyncio.Lock()


def _iso(dt: datetime) -> str:
    """Serialize a datetime to ISO-8601 (always timezone-aware)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _load() -> dict:
    """Read the store file; an empty/corrupted file yields a fresh dict."""
    if not os.path.exists(POSTS_DB_PATH):
        return {}
    try:
        with open(POSTS_DB_PATH, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(POSTS_DB_PATH) or ".", exist_ok=True)
    with open(POSTS_DB_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _prune(data: dict, now: datetime) -> dict:
    """Drop finished records older than RETENTION_DAYS."""
    cutoff = now - timedelta(days=RETENTION_DAYS)
    out = {}
    for key, entry in data.items():
        if entry.get("status") in _KEEP_STATUSES:
            out[key] = entry
            continue
        finished = entry.get("finished_at")
        if not finished or datetime.fromisoformat(finished) >= cutoff:
            out[key] = entry
    return out


async def get_post(key: str) -> dict | None:
    """Return the record for a post key, or None if never seen."""
    async with _lock:
        return _load().get(key)


async def ensure(key: str, post_date: datetime, act_at: datetime,
                 status: str = "pending") -> dict:
    """
    Insert a record for a freshly-discovered post (no-op if it exists).
    Returns the stored record either way.
    """
    async with _lock:
        data = _load()
        if key in data:
            return data[key]
        entry = {
            "status": status,
            "post_date": _iso(post_date),
            "act_at": _iso(act_at),
            "first_seen": _iso(datetime.now(timezone.utc)),
        }
        data[key] = entry
        _save(_prune(data, datetime.now(timezone.utc)))
        return entry


async def mark(key: str, status: str, **extra) -> None:
    """
    Transition a record to a new status.

    Finished statuses (done/failed/skipped) also get a finished_at stamp.
    Extra kwargs are merged into the record (e.g. reason, summary).
    """
    async with _lock:
        data = _load()
        entry = data.get(key) or {}
        entry["status"] = status
        if status not in _KEEP_STATUSES:
            entry["finished_at"] = _iso(datetime.now(timezone.utc))
        entry.update(extra)
        data[key] = entry
        _save(_prune(data, datetime.now(timezone.utc)))
```

## File: utils/ui.py
```python
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
        )
    except MessageNotModified:
        return message
```

## File: __init__.py
```python

```

## File: config.py
```python
"""
config.py — Load and expose all environment variables.
"""

import logging
import os
from dotenv import load_dotenv

load_dotenv()

# Bot token from @BotFather
BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# Telegram API credentials
API_ID: int = int(os.environ["API_ID"])
API_HASH: str = os.environ["API_HASH"]

# Admin user IDs (comma-separated in .env).
# Single source of truth: these admins manage the bot panel AND receive
# every event log (success, failure, summaries, alerts).
ADMIN_IDS: list[int] = [
    int(uid.strip()) for uid in os.environ["ADMIN_IDS"].split(",") if uid.strip()
]

if not ADMIN_IDS:
    logging.warning(
        "ADMIN_IDS is empty — nobody can use the bot panel and no event "
        "logs will be delivered. Fill ADMIN_IDS in .env!"
    )

# Path to the JSON accounts database
DB_PATH: str = os.getenv("DB_PATH", "data/accounts.json")

# Directory where per-account session files are stored
SESSIONS_DIR: str = os.getenv("SESSIONS_DIR", "sessions")

# ── Channel auto-reactions + Star gifting ─────────────────────────────────────


def _parse_channel(raw_value: str) -> int | str:
    """Accept numeric channel IDs (-100...) as int, usernames as str."""
    raw_value = raw_value.strip()
    if raw_value.lstrip("-").isdigit():
        return int(raw_value)
    return raw_value


# Channel to monitor (bot must be an admin there)
TARGET_CHANNEL: int | str = _parse_channel(os.getenv("TARGET_CHANNEL", "@mychannel"))

# Keyword that must appear in a post's text or caption for it to be processed
POST_KEYWORD: str = os.getenv("POST_KEYWORD", "giveaway")

# Minutes to wait after detecting a qualifying post before acting
DELAY_MINUTES: int = int(os.getenv("DELAY_MINUTES", "13"))

# Random number of accounts to select is drawn between these two
MIN_ACCOUNTS: int = int(os.getenv("MIN_ACCOUNTS", "2"))
MAX_ACCOUNTS: int = int(os.getenv("MAX_ACCOUNTS", "5"))

# Random Star amount per account is drawn between these two.
# An account is only eligible if its balance is >= MAX_STARS.
MIN_STARS: int = int(os.getenv("MIN_STARS", "3"))
MAX_STARS: int = int(os.getenv("MAX_STARS", "5"))

# Allowed reactions (comma-separated emoji list)
REACTIONS: list[str] = [
    r.strip() for r in os.getenv("REACTIONS", "❤️,👍,🔥,🎉").split(",") if r.strip()
]

# ── Channel polling (replaces live on_message detection) ─────────────────────

# Minutes between poll cycles: each cycle fetches the latest posts of the
# target channel and decides which ones must be processed.
POLL_INTERVAL_MINUTES: int = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))

# How many latest posts each poll cycle fetches.
POLL_FETCH_COUNT: int = int(os.getenv("POLL_FETCH_COUNT", "10"))

# A keyword post first seen older than this is skipped as stale (protection
# against acting on long-gone giveaways, e.g. right after a bot restart).
POST_MAX_AGE_MINUTES: int = int(os.getenv("POST_MAX_AGE_MINUTES", "60"))

# JSON store of already-checked posts (prevents re-processing on every cycle)
POSTS_DB_PATH: str = os.getenv("POSTS_DB_PATH", "data/processed_posts.json")
```
