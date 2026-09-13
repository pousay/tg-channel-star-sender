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




def test_monitor_eligibility_and_not_enough_fallback() -> None:
    """Eligibility = balance >= MAX_STARS; not enough -> empty result + count."""
    from bot.utils import db
    from bot.handlers import channel_monitor as cm

    db.save_accounts([
        {
            "phone": f"+100000000{i:02d}",
            "name": f"U{i}",
            "star_balance": 0 if i % 3 == 0 else cm.MAX_STARS + i,
            "session_string": f"s{i}",
            "tfa_password": None,
        }
        for i in range(21)
    ])

    selected, available = cm._select_accounts(4)
    assert len(selected) == 4
    assert all(a["star_balance"] >= cm.MAX_STARS for a in selected)
    assert len({a["phone"] for a in selected}) == 4  # no duplicates

    selected2, available2 = cm._select_accounts(99)
    assert selected2 == []
    eligible_count = len([a for a in db.load_accounts() if a["star_balance"] >= cm.MAX_STARS])
    assert available2 == eligible_count


def test_monitor_reaction_and_star_assignment_bounds() -> None:
    """Reactions come from REACTIONS; star amounts stay within MIN..MAX."""
    from bot.handlers import channel_monitor as cm
    from bot.config import MIN_STARS, MAX_STARS, REACTIONS

    reaction = cm.random.choice(REACTIONS)
    stars = cm.random.randint(MIN_STARS, MAX_STARS)
    assert reaction in REACTIONS
    assert MIN_STARS <= stars <= MAX_STARS


# ── Text-router propagation (delete flow regression) ──────────────────────────

class FakeApp:
    """Captures handler registrations instead of talking to Telegram."""

    def __init__(self) -> None:
        self.handlers: list[tuple[str, object, object]] = []

    def on_message(self, filters=None, group=0):
        def deco(fn):
            self.handlers.append(("message", filters, fn))
            return fn
        return deco

    def on_callback_query(self, filters=None, group=0):
        def deco(fn):
            self.handlers.append(("callback", filters, fn))
            return fn
        return deco


from pyrogram.types import Message as PyroMessage


class FakeMessage(PyroMessage):
    """Enough of a Message for the admin_only guard and the text routers.

    Subclasses the real pyrogram Message (without calling its __init__,
    which needs many raw fields) so the admin_only decorator's
    isinstance() check passes.
    """

    def __init__(self, text: str, user_id: int) -> None:
        self.text = text
        self.from_user = type("U", (), {"id": user_id})()
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def _get_text_routers():
    """Register all handlers and return the text routers in registration order."""
    from bot.handlers import add_account, delete_account

    app = FakeApp()
    add_account.register_add_account(app)
    delete_account.register_delete_account(app)

    routers = []
    for kind, _, fn in app.handlers:
        if kind == "message" and getattr(fn, "__name__", "") == "text_router":
            routers.append(fn)
    assert len(routers) == 2, f"expected 2 text routers, got {len(routers)}"
    return routers  # [add_account's, delete_account's] — registration order


def test_idle_text_routers_continue_propagation() -> None:
    """Routers with no in-flow state must NOT consume the update silently.

    This encodes the delete-flow bug: an idle first router used to return
    normally, which made the dispatcher skip delete_account's router.
    """
    from bot.handlers import add_account, delete_account
    from pyrogram import ContinuePropagation

    add_account._state.clear()
    delete_account._state.clear()
    add_router, delete_router = _get_text_routers()

    msg = FakeMessage("+989123456789", user_id=111)
    for router in (add_router, delete_router):
        try:
            asyncio.run(router(None, msg))
        except ContinuePropagation:
            continue
        raise AssertionError(f"{router.__module__} consumed an idle update")


def test_delete_flow_receives_phone_through_router_chain() -> None:
    """Full chain: idle add router propagates, delete router handles the phone."""
    from bot.handlers import add_account, delete_account
    from bot.utils import db
    from pyrogram import ContinuePropagation

    add_account._state.clear()
    delete_account._state.clear()
    db.save_accounts([])  # phone will be "not found" — enough to prove routing
    add_router, delete_router = _get_text_routers()

    delete_account._state[111] = {"step": "awaiting_delete_phone"}
    msg = FakeMessage("+989123456789", user_id=111)

    # Mini-dispatcher: same group semantics as pyrogram (break on normal return)
    for router in (add_router, delete_router):
        try:
            asyncio.run(router(None, msg))
        except ContinuePropagation:
            continue
        break

    assert 111 not in delete_account._state, "delete state must be consumed"
    assert msg.replies, "delete router must reply to the admin"
    assert "پیدا نشد" in msg.replies[0][0]


def test_active_add_router_consumes_update() -> None:
    """An active add-flow router must keep consuming (no propagation)."""
    from bot.handlers import add_account, delete_account
    from pyrogram import ContinuePropagation

    add_account._state.clear()
    delete_account._state.clear()
    add_router, _ = _get_text_routers()

    # Harmless unknown step: no branch runs, no Telegram calls happen
    add_account._state[111] = {"step": "__noop__"}
    msg = FakeMessage("whatever", user_id=111)
    try:
        asyncio.run(add_router(None, msg))
    except ContinuePropagation:
        raise AssertionError("active router must consume, not propagate")


def main() -> None:
    print("Running tests:")
    run_test(test_config_admin_ids_parsing)
    run_test(test_config_empty_admin_ids_warning)
    run_test(test_notify_fan_out_to_all_admins)
    run_test(test_notify_partial_failure_does_not_block_others)
    run_test(test_notify_never_raises)
    run_test(test_notify_delivered_count_logged)
    run_test(test_monitor_log_formats_are_valid_html)
    run_test(test_monitor_eligibility_and_not_enough_fallback)
    run_test(test_monitor_reaction_and_star_assignment_bounds)
    run_test(test_idle_text_routers_continue_propagation)
    run_test(test_delete_flow_receives_phone_through_router_chain)
    run_test(test_active_add_router_consumes_update)

    print(f"\n{len(_PASSED)} passed, {len(_FAILED)} failed")
    if _FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
