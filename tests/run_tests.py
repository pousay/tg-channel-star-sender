"""
run_tests.py — Lightweight test runner (plain Python, no dependencies).

Run from the project root:
    python tests/run_tests.py
"""

import asyncio
import os
import sys

# Test environment for bot.config — must be set before importing bot modules
os.environ["BOT_TOKEN"] = "1:test"
os.environ["API_ID"] = "1"
os.environ["API_HASH"] = "x"
os.environ["ADMIN_IDS"] = "111,222"
os.environ.setdefault("TARGET_CHANNEL", "@testch")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PASSED: list[str] = []
_FAILED: list[tuple[str, Exception]] = []


def run_test(fn) -> None:
    """Run a single test function (sync or async) and record the outcome."""
    try:
        result = fn()
        if asyncio.iscoroutine(result):
            asyncio.run(result)
        _PASSED.append(fn.__name__)
        print(f"  ✓ {fn.__name__}")
    except Exception as e:  # noqa: BLE001 — the runner must not stop early
        _FAILED.append((fn.__name__, e))
        print(f"  ✗ {fn.__name__}: {e}")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_config_admin_ids_parsing() -> None:
    """ADMIN_IDS must parse a comma-separated list, trimming spaces."""
    import bot.config as cfg

    assert cfg.ADMIN_IDS == [111, 222], cfg.ADMIN_IDS


def test_config_empty_admin_ids_warning() -> None:
    """An empty ADMIN_IDS must trigger a loud startup warning."""
    import importlib
    import logging

    import bot.config as cfg

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logging.getLogger().addHandler(Capture())
    logging.getLogger().setLevel(logging.WARNING)
    try:
        os.environ["ADMIN_IDS"] = "  , ,"
        importlib.reload(cfg)
        assert any(
            r.levelno == logging.WARNING and "ADMIN_IDS" in r.getMessage()
            for r in records
        ), "expected an ADMIN_IDS warning"
    finally:
        os.environ["ADMIN_IDS"] = "111,222"
        importlib.reload(cfg)
        logging.getLogger().handlers = [
            h for h in logging.getLogger().handlers if not isinstance(h, Capture)
        ]


# ── Notify fan-out ────────────────────────────────────────────────────────────

class StubBot:
    """Minimal client stub that records send_message calls."""

    def __init__(self, fail_for: set[int] | None = None) -> None:
        self.sent: list[int] = []
        self.fail_for = fail_for or set()

    async def send_message(self, chat_id: int, text: str, parse_mode: str = None) -> None:
        if chat_id in self.fail_for:
            raise RuntimeError("delivery failed")
        self.sent.append(chat_id)


def test_notify_fan_out_to_all_admins() -> None:
    """Every admin in ADMIN_IDS must receive the log message."""
    from bot.utils import notify

    bot = StubBot()
    asyncio.run(notify.notify_admin(bot, "hello"))
    assert bot.sent == [111, 222], bot.sent


def test_notify_partial_failure_does_not_block_others() -> None:
    """One admin failing to receive must not stop delivery to the rest."""
    from bot.utils import notify

    bot = StubBot(fail_for={111})
    asyncio.run(notify.notify_admin(bot, "hello"))
    assert bot.sent == [222], bot.sent


def test_notify_never_raises() -> None:
    """Total delivery failure must not raise back to the caller."""
    from bot.utils import notify

    bot = StubBot(fail_for={111, 222})
    asyncio.run(notify.notify_admin(bot, "hello"))  # must not raise
    assert bot.sent == []


def test_notify_delivered_count_logged() -> None:
    """A debug log must report how many admins received the message."""
    import logging

    from bot.utils import notify

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logging.getLogger().addHandler(Capture())
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        bot = StubBot(fail_for={222})
        asyncio.run(notify.notify_admin(bot, "hello"))
        assert any(
            r.levelno == logging.DEBUG and "1/2" in r.getMessage()
            for r in records
        ), "expected a '1/2 admins' debug log"
    finally:
        logging.getLogger().handlers = [
            h for h in logging.getLogger().handlers if not isinstance(h, Capture)
        ]


# ── Channel monitor ───────────────────────────────────────────────────────────

def test_monitor_log_formats_are_valid_html() -> None:
    """Every admin-facing log format must parse as valid HTML."""
    import asyncio

    from pyrogram import Client
    from pyrogram.parser import Parser

    from bot.handlers import channel_monitor as cm

    app = Client("t", api_id=1, api_hash="x", bot_token="1:test", in_memory=True)
    parser = Parser(app)
    samples = [
        f"✅ اکانت <code>+98912</code> (A&lt;li&gt;) — ری‌اکشن ❤️ ارسال شد — "
        f"پست: https://t.me/c/1/2 — 🕒 {cm._now()}",
        f"❌ اکانت <code>+98912</code> (Ali) — ارسال <b>4</b> ستاره ناموفق — "
        f"دلیل: محدودیت موقت تلگرام (30 ثانیه)\nپست: https://t.me/c/1/2",
        "⚠️ پردازش پست https://t.me/c/1/2 ممکن نشد — به <b>4</b> اکانت واجد نیاز بود، "
        "اما فقط <b>1</b> اکانت موجودی ≥ <b>5</b> ستاره داشت.\n"
        "هیچ ستاره یا ری‌اکشنی ارسال نشد.",
        "📊 <b>خلاصه پست</b>\n🔗 پست: https://t.me/c/1/2\n"
        "👥 اکانت‌های انتخاب‌شده: <b>3</b>\n✅ عملیات موفق: <b>5</b>\n"
        "❌ عملیات ناموفق: <b>1</b>\n⭐ مجموع ستاره ارسال‌شده: <b>16</b>",
    ]
    for sample in samples:
        asyncio.run(parser.parse(sample, None))




def main() -> None:
    print("Running tests:")
    run_test(test_config_admin_ids_parsing)
    run_test(test_config_empty_admin_ids_warning)
    run_test(test_notify_fan_out_to_all_admins)
    run_test(test_notify_partial_failure_does_not_block_others)
    run_test(test_notify_never_raises)
    run_test(test_notify_delivered_count_logged)
    run_test(test_monitor_log_formats_are_valid_html)

    print(f"\n{len(_PASSED)} passed, {len(_FAILED)} failed")
    if _FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
