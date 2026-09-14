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
