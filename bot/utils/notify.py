"""
notify.py — Helper for sending detailed event logs to the admin.

Every critical event (success, failure, or "not enough accounts") goes
through here. Notification failures are logged to the console but never
crash the caller — "no silent failures" applies to events, and a failed
delivery is still recorded locally.
"""

import logging

from pyrogram import Client

from bot.config import ADMIN_ID


async def notify_admin(client: Client, text: str) -> None:
    """Send a log message to the admin; never raise on failure."""
    try:
        await client.send_message(ADMIN_ID, text, parse_mode="html")
    except Exception as e:
        # Keep a local trace so the event is never fully lost
        logging.warning("Failed to deliver admin notification: %s", e)
