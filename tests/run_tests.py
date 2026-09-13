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


def main() -> None:
    print("Running tests:")
    run_test(test_config_admin_ids_parsing)

    print(f"\n{len(_PASSED)} passed, {len(_FAILED)} failed")
    if _FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
