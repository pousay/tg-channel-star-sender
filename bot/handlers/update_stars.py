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
from bot.utils.ui import esc, safe_edit, main_menu_button


def register_update_stars(app: Client) -> None:
    """Register the Update Stars Balance callback handler."""

    @app.on_callback_query(filters.regex("^update_stars$"))
    @admin_only
    async def cb_update_stars(client: Client, query: CallbackQuery) -> None:
        await query.answer()
        accounts = load_accounts()

        if not accounts:
            await query.answer("📭 هنوز هیچ اکانتی ذخیره نشده است.", show_alert=True)
            return

        total = len(accounts)

        # Initial progress message
        status = await safe_edit(
            query.message,
            f"⏳ در حال به‌روزرسانی موجودی ستاره‌ها… (0/{total})",
        )

        updated: list[tuple[str, int]] = []   # (phone, new_balance)
        failed: list[tuple[str, str]] = []    # (phone, reason)

        for i, account in enumerate(accounts, start=1):
            phone = account.get("phone", "نامشخص")
            session_string = account.get("session_string")

            if not session_string:
                failed.append((phone, "سشنی برای این اکانت ذخیره نشده است."))
                await safe_edit(status, f"⏳ در حال به‌روزرسانی موجودی ستاره‌ها… ({i}/{total})")
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
                failed.append((phone, f"محدودیت موقت تلگرام — {e.value} ثانیه دیگر تلاش کنید."))
                await asyncio.sleep(e.value)
            except AuthKeyUnregistered:
                failed.append((phone, "سشن باطل شده یا از اکانت خارج شده است."))
            except UserDeactivated:
                failed.append((phone, "اکانت غیرفعال (حذف) شده است."))
            except Unauthorized:
                failed.append((phone, "سشن دیگر معتبر نیست."))
            except Exception as e:
                failed.append((phone, f"{type(e).__name__}: {e}"))
            finally:
                try:
                    await user_client.disconnect()
                except Exception:
                    pass

                # Live progress update
                await safe_edit(status, f"⏳ در حال به‌روزرسانی موجودی ستاره‌ها… ({i}/{total})")

        # ── Build the final report ────────────────────────────────────────────
        lines = [
            f"⭐ <b>به‌روزرسانی موجودی ستاره‌ها تمام شد!</b>\n\n"
            f"✅ موفق: <b>{len(updated)}</b>\n"
            f"❌ ناموفق: <b>{len(failed)}</b>"
        ]

        if updated:
            lines.append("\n<b>✅ به‌روز شده:</b>")
            for phone, balance in updated:
                lines.append(f"✔️ <code>{esc(phone)}</code> — {balance} ستاره")

        if failed:
            lines.append("\n<b>❌ ناموفق:</b>")
            for phone, reason in failed:
                lines.append(f"✖️ <code>{esc(phone)}</code>\n└─ {esc(reason)}")

        await safe_edit(status, "\n".join(lines), main_menu_button())
