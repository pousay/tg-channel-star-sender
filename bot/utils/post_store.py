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
