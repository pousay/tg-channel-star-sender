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


def main() -> None:
    print("Running tests:")
    run_test(test_config_admin_ids_parsing)
    run_test(test_config_empty_admin_ids_warning)
    run_test(test_notify_fan_out_to_all_admins)
    run_test(test_notify_partial_failure_does_not_block_others)
    run_test(test_notify_never_raises)

    print(f"\n{len(_PASSED)} passed, {len(_FAILED)} failed")
    if _FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
