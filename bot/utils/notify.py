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
