"""
channel_monitor.py — Automated reactions + Star gifting to channel posts.

End-to-end flow for every new post in TARGET_CHANNEL:
  1. Detect a new post (deduplicated, albums counted once).
  2. Filter: the post must contain POST_KEYWORD in its text or caption.
  3. Wait DELAY_MINUTES minutes.
  4. Pick a random number of accounts between MIN_ACCOUNTS and MAX_ACCOUNTS.
  5. Validate: every selected account must have balance >= MAX_STARS.
     Not enough eligible accounts → alert the admin, send nothing.
  6. Assign a random reaction (from REACTIONS) to each account.
  7. Assign a random Star amount (MIN_STARS..MAX_STARS) to each account.
  8. Per account: connect via saved session, send the reaction, then the
     Stars (paid reaction). Errors are handled per account.
  9. Log every action to the admin (success/failure, exact reason,
     post link, timestamp) and finish with a per-post summary.
"""

import asyncio
import logging
import random
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.errors import (
    AuthKeyUnregistered,
    FloodWait,
    RPCError,
    Unauthorized,
    UserDeactivated,
)

from bot.config import (
    ADMIN_ID,
    DELAY_MINUTES,
    MAX_ACCOUNTS,
    MAX_STARS,
    MIN_ACCOUNTS,
    MIN_STARS,
    POST_KEYWORD,
    REACTIONS,
    TARGET_CHANNEL,
)
from bot.utils.db import load_accounts
from bot.utils.notify import notify_admin
from bot.utils.ui import esc

# Deduplication set: (chat_id, media_group_id or message_id) already seen
_seen_posts: set[tuple[int, int | str]] = set()

# Small pause between accounts to reduce flood risk
_PER_ACCOUNT_PAUSE_SECONDS = 1.5


def _post_link(message) -> str:
    """Build a public link for the post, or a readable fallback for private chats."""
    chat = message.chat
    if chat is not None and chat.username:
        return f"https://t.me/{chat.username}/{message.id}"
    return f"پست #{message.id} در {TARGET_CHANNEL}"


def _contains_keyword(message) -> bool:
    """True if POST_KEYWORD appears (case-insensitive) in the text or caption."""
    keyword = POST_KEYWORD.lower()
    text = message.text or ""
    caption = message.caption or ""
    return keyword in text.lower() or keyword in caption.lower()


def _select_accounts(count: int) -> tuple[list[dict], int]:
    """
    Randomly select `count` eligible accounts (balance >= MAX_STARS).

    Returns (selected, available):
      - selected: list of accounts (shorter than `count` if not enough)
      - available: how many eligible accounts existed in the pool
    """
    accounts = load_accounts()
    eligible = [
        acc for acc in accounts
        if int(acc.get("star_balance", 0) or 0) >= MAX_STARS
    ]
    if len(eligible) < count:
        return [], len(eligible)
    return random.sample(eligible, count), len(eligible)


def _now() -> str:
    """Human-readable timestamp for the log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _failure_reason(e: Exception) -> str:
    """Translate a per-account exception into a short Persian reason."""
    if isinstance(e, FloodWait):
        return f"محدودیت موقت تلگرام ({e.value} ثانیه)"
    if isinstance(e, (AuthKeyUnregistered, Unauthorized)):
        return "سشن باطل شده یا از اکانت خارج شده است"
    if isinstance(e, UserDeactivated):
        return "اکانت غیرفعال (حذف) شده است"
    if isinstance(e, RPCError):
        return f"خطای تلگرام: {esc(e)}"
    return f"{type(e).__name__}: {esc(e)}"


async def _process_post(client: Client, message) -> None:
    """Run the whole delayed reaction + star-gifting pipeline for one post."""
    link = _post_link(message)

    # ── Step 3: wait before acting ────────────────────────────────────────
    await asyncio.sleep(DELAY_MINUTES * 60)

    # ── Steps 4–5: pick and validate accounts ─────────────────────────────
    count = random.randint(MIN_ACCOUNTS, MAX_ACCOUNTS)
    selected, available = _select_accounts(count)

    if len(selected) < count:
        await notify_admin(
            client,
            f"⚠️ پردازش پست {link} ممکن نشد — به <b>{count}</b> اکانت واجد نیاز بود، "
            f"اما فقط <b>{available}</b> اکانت موجودی ≥ <b>{MAX_STARS}</b> ستاره داشت.\n"
            f"هیچ ستاره یا ری‌اکشنی ارسال نشد.",
        )
        return

    ok_actions = 0    # successful reactions + star sends
    fail_actions = 0  # failed reactions + star sends
    total_stars = 0   # stars actually delivered

    # ── Steps 6–8: act with each account ──────────────────────────────────
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

        # Detailed per-account log to the admin
        await notify_admin(client, "\n".join(lines))
        await asyncio.sleep(_PER_ACCOUNT_PAUSE_SECONDS)

    # ── Step 9: overall summary for this post ─────────────────────────────
    await notify_admin(
        client,
        f"📊 <b>خلاصه پست</b>\n"
        f"🔗 پست: {link}\n"
        f"👥 اکانت‌های انتخاب‌شده: <b>{count}</b>\n"
        f"✅ عملیات موفق: <b>{ok_actions}</b>\n"
        f"❌ عملیات ناموفق: <b>{fail_actions}</b>\n"
        f"⭐ مجموع ستاره ارسال‌شده: <b>{total_stars}</b>",
    )


def register_channel_monitor(app: Client) -> None:
    """Register the target-channel message handler."""

    @app.on_message(filters.chat(TARGET_CHANNEL) & ~filters.service & ~filters.outgoing)
    async def channel_post_handler(client: Client, message) -> None:
        # ── Step 1: detect a new post (albums handled once) ───────────────
        key = (message.chat.id, message.media_group_id or message.id)
        if key in _seen_posts:
            return
        _seen_posts.add(key)

        # ── Step 2: keyword filter — skip posts without the keyword ───────
        if not _contains_keyword(message):
            logging.info("Post %s skipped — keyword %r not found", message.id, POST_KEYWORD)
            return

        logging.info(
            "Post %s matched keyword %r — processing in %s minutes",
            message.id, POST_KEYWORD, DELAY_MINUTES,
        )

        # Steps 3–9 run in a background task so the handler returns instantly
        asyncio.create_task(_process_post(client, message))
