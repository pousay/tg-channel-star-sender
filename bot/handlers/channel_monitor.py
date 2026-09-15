"""
channel_monitor.py — Live channel-post detection + delayed reactions/Stars.

How detection works:
  The BOT ITSELF is added as an admin/member of the private target channel.
  Telegram then pushes every new post to the bot live (no polling, no user
  account needed just to "watch" the channel). We only reach for a user
  account when it's time to actually react / send Stars, since bots cannot
  send paid reactions.

Pipeline per post:
  1. Dedupe — an album (media_group_id) fires one event per media item;
     only the first is scheduled.
  2. Wait DELAY_MINUTES (in the background, non-blocking).
  3. Pick a random subset of accounts (MIN_ACCOUNTS..MAX_ACCOUNTS) with
     balance >= MAX_STARS — this subset is who will send Stars.
  4. Every saved account sends a reaction; results are batched into ONE
     admin message (post link once at the top, then one line per account).
     Accounts in the Star subset also send a random Star amount (paid
     reaction) — those results are logged per account, as before.
  5. Log a summary to all admins via bot/utils/notify.py.

Note: because scheduling lives in memory, posts still "in the delay window"
at the moment the bot restarts are lost — acceptable for this scale. If that
ever matters, persist pending posts to disk and re-schedule them on startup.
"""

import asyncio
import logging
import random
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.config import (
    DELAY_MINUTES,
    MAX_ACCOUNTS,
    MAX_STARS,
    MIN_ACCOUNTS,
    MIN_STARS,
    REACTIONS,
    TARGET_CHANNEL,
)
from bot.utils.db import load_accounts
from bot.utils.notify import notify_admin
from bot.utils.ui import esc

# In-memory dedupe of posts already scheduled/processed this run.
_seen_keys: set[str] = set()

# Small pause between accounts to reduce flood risk
_PER_ACCOUNT_PAUSE_SECONDS = 1.5


# ── Pure helpers (unit-testable, no Telegram I/O) ─────────────────────────────


def _post_link(message: Message) -> str:
    """Build a clickable link for the post (works for private channels too)."""
    chat = message.chat
    if chat is not None and chat.username:
        return f"https://t.me/{chat.username}/{message.id}"
    if chat is not None and str(chat.id).startswith("-100"):
        return f"https://t.me/c/{str(chat.id)[4:]}/{message.id}"
    return f"پست #{message.id} در {TARGET_CHANNEL}"


def _post_key(message: Message) -> str:
    """Stable dedupe key: albums share one media_group_id, count them once."""
    chat_id = message.chat.id if message.chat else 0
    return f"{chat_id}:{message.media_group_id or message.id}"


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


# ── Per-post pipeline ─────────────────────────────────────────────────────────


async def _process_post(client: Client, message: Message, link: str) -> None:
    """
    Run the reaction + star-gifting pipeline for one due post.

    Reactions: sent by EVERY saved account; results batched into one message.
    Stars: sent only by a randomly-selected subset (MIN_ACCOUNTS..MAX_ACCOUNTS)
    that has balance >= MAX_STARS — unchanged from before.
    """
    all_accounts = load_accounts()
    count = random.randint(MIN_ACCOUNTS, MAX_ACCOUNTS)
    star_selected, available = _select_accounts(count)
    star_phones = {acc.get("phone") for acc in star_selected}

    if len(star_selected) < count:
        await notify_admin(
            client,
            f"⚠️ ارسال ستاره برای پست {link} ممکن نشد — به <b>{count}</b> اکانت واجد نیاز بود، "
            f"اما فقط <b>{available}</b> اکانت موجودی ≥ <b>{MAX_STARS}</b> ستاره داشت.\n"
            f"ری‌اکشن‌ها طبق روال برای همه اکانت‌ها ارسال می‌شود.",
        )

    ok_actions = 0
    fail_actions = 0
    total_stars = 0
    reaction_lines: list[str] = []

    for account in all_accounts:
        phone = account.get("phone", "نامشخص")
        name = esc(account.get("name", "نامشخص"))
        reaction = random.choice(REACTIONS)

        user_client = Client(
            "actor",
            session_string=account.get("session_string"),
            in_memory=True,
        )

        try:
            await user_client.connect()
        except Exception as e:
            fail_actions += 1
            reaction_lines.append(
                f"❌ اکانت <code>{phone}</code> ({name}) — اتصال با سشن ناموفق بود — "
                f"دلیل: {_failure_reason(e)}"
            )
            continue

        try:
            await user_client.send_reaction(
                TARGET_CHANNEL, message_id=message.id, emoji=reaction
            )
            ok_actions += 1
            reaction_lines.append(
                f"✅ اکانت <code>{phone}</code> ({name}) — ری‌اکشن {reaction} ارسال شد"
            )
        except Exception as e:
            fail_actions += 1
            reaction_lines.append(
                f"❌ اکانت <code>{phone}</code> ({name}) — ارسال ری‌اکشن {reaction} ناموفق — "
                f"دلیل: {_failure_reason(e)}"
            )

        if phone in star_phones:
            stars = random.randint(MIN_STARS, MAX_STARS)
            try:
                await user_client.send_paid_reaction(
                    TARGET_CHANNEL, message_id=message.id, amount=stars
                )
                ok_actions += 1
                total_stars += stars
                await notify_admin(
                    client,
                    f"✅ اکانت <code>{phone}</code> ({name}) — <b>{stars}</b> ستاره ارسال شد — "
                    f"پست: {link} — 🕒 {_now()}",
                )
            except Exception as e:
                fail_actions += 1
                await notify_admin(
                    client,
                    f"❌ اکانت <code>{phone}</code> ({name}) — ارسال <b>{stars}</b> ستاره ناموفق — "
                    f"دلیل: {_failure_reason(e)}\nپست: {link}",
                )

        await user_client.disconnect()
        await asyncio.sleep(_PER_ACCOUNT_PAUSE_SECONDS)

    await notify_admin(
        client,
        f"🔗 پست: {link}\n🕒 {_now()}\n\n" + "\n".join(reaction_lines),
    )

    await notify_admin(
        client,
        f"📊 <b>خلاصه پست</b>\n"
        f"🔗 پست: {link}\n"
        f"👥 اکانت‌های ری‌اکشن‌دهنده: <b>{len(all_accounts)}</b>\n"
        f"⭐ اکانت‌های ستاره‌دهنده: <b>{len(star_selected)}</b>\n"
        f"✅ عملیات موفق: <b>{ok_actions}</b>\n"
        f"❌ عملیات ناموفق: <b>{fail_actions}</b>\n"
        f"⭐ مجموع ستاره ارسال‌شده: <b>{total_stars}</b>",
    )


async def _delayed_process(client: Client, message: Message, link: str) -> None:
    """Wait DELAY_MINUTES, then run the pipeline. Scheduled as a background task."""
    try:
        await asyncio.sleep(DELAY_MINUTES * 60)
        await _process_post(client, message, link)
    except Exception as e:
        logging.exception("Post processing crashed")
        await notify_admin(
            client,
            f"🚨 <b>پردازش پست با خطا متوقف شد</b>\n"
            f"🔗 پست: {link}\n"
            f"❌ خطا: <code>{esc(e)}</code>",
        )


# ── Live handler ───────────────────────────────────────────────────────────────


def register_channel_monitor(app: Client) -> None:
    """Register the live channel-post listener (bot must be admin/member there)."""

    @app.on_message(filters.chat(TARGET_CHANNEL))
    async def on_channel_post(client: Client, message: Message) -> None:
        logging.info("new post detected in channel")
        if message.service:
            return

        key = _post_key(message)
        if key in _seen_keys:
            return  # album duplicate

        _seen_keys.add(key)
        link = _post_link(message)
        logging.info(
            "New post %s detected — scheduled in %s min (key=%s)",
            message.id,
            DELAY_MINUTES,
            key,
        )
        asyncio.create_task(_delayed_process(client, message, link))
