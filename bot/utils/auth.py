"""
auth.py — Admin-only access guard decorator.
"""

from functools import wraps
from pyrogram.types import Message, CallbackQuery
from bot.config import ADMIN_IDS


def admin_only(func):
    """
    Decorator that silently ignores messages/callbacks from non-admin users.
    Works for both Message and CallbackQuery handlers.
    """
    @wraps(func)
    async def wrapper(client, update, *args, **kwargs):
        if isinstance(update, CallbackQuery):
            user_id = update.from_user.id
        elif isinstance(update, Message):
            user_id = update.from_user.id
        else:
            return

        if user_id not in ADMIN_IDS:
            return  # Silently ignore non-admins

        return await func(client, update, *args, **kwargs)

    return wrapper
