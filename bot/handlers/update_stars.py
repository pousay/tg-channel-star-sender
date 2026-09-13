"""
update_stars.py — Handles the "Update Stars Balance" flow.

Walks through every saved account, connects using its saved session string,
fetches the current Telegram Stars balance, and updates the JSON database.

Accounts that cannot be accessed (revoked session, connection failure, etc.)
are reported to the admin with the reason for the failure.
"""

import asyncio

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    AuthKeyUnregistered,
    UserDeactivated,
    Unauthorized,
    FloodWait,
)

from bot.config import API_ID, API_HASH
from bot.utils.auth import admin_only
from bot.utils.db import load_accounts, upsert_account


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ])


def register_update_stars(app: Client) -> None:
    """Register the Update Stars Balance callback handler."""

    @app.on_callback_query(filters.regex("^update_stars$"))
    @admin_only
    async def cb_update_stars(client: Client, query: CallbackQuery) -> None:
        accounts = load_accounts()

        if not accounts:
            await query.answer("📭 No accounts saved yet.", show_alert=True)
            return

        # Initial progress message
        status = await query.message.edit_text(
            f"⏳ Updating star balances… (0/{len(accounts)})"
        )

        updated: list[tuple[str, int]] = []   # (phone, new_balance)
        failed: list[tuple[str, str]] = []    # (phone, reason)

        for i, account in enumerate(accounts, start=1):
            phone = account.get("phone", "unknown")
            session_string = account.get("session_string")

            if not session_string:
                failed.append((phone, "No session string saved for this account."))
                await status.edit_text(
                    f"⏳ Updating star balances… ({i}/{len(accounts)})"
                )
                continue

            user_client = Client(
                "updater",
                session_string=session_string,
                in_memory=True,
            )

            try:
                await user_client.connect()
                balance = int(await user_client.get_stars_balance())
                account["star_balance"] = balance
                upsert_account(account)
                updated.append((phone, balance))
            except FloodWait as e:
                failed.append((phone, f"Flood wait — retry in {e.value} seconds."))
                await asyncio.sleep(e.value)
            except AuthKeyUnregistered:
                failed.append((phone, "Session revoked / logged out."))
            except UserDeactivated:
                failed.append((phone, "Account is deactivated."))
            except Unauthorized:
                failed.append((phone, "Unauthorized — session is no longer valid."))
            except Exception as e:
                failed.append((phone, f"{type(e).__name__}: {e}"))
            finally:
                try:
                    await user_client.disconnect()
                except Exception:
                    pass

                # Live progress update
                await status.edit_text(
                    f"⏳ Updating star balances… ({i}/{len(accounts)})"
                )

        # ── Build the final report ────────────────────────────────────────────
        lines = [
            f"⭐ **Star balance update finished** — {len(updated)} ok, {len(failed)} failed.\n"
        ]

        if updated:
            lines.append("**Updated:**")
            for phone, balance in updated:
                lines.append(f"✅ <code>{phone}</code> — {balance} stars")

        if failed:
            lines.append("\n**Failed:**")
            for phone, reason in failed:
                lines.append(f"❌ <code>{phone}</code> — {reason}")

        await status.edit_text(
            "\n".join(lines),
            reply_markup=_main_menu_keyboard(),
        )
