"""
db.py — JSON-based database helpers for reading and writing account records.

Schema per account:
{
    "phone": "+1234567890",
    "name": "John Doe",
    "star_balance": 0,
    "session_string": "...",
    "tfa_password": null
}
"""

import json
import os
from typing import Optional
from bot.config import DB_PATH


def _ensure_db() -> None:
    """Create the database file with an empty list if it doesn't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w") as f:
            json.dump([], f, indent=2)


def load_accounts() -> list[dict]:
    """Return all accounts from the JSON database."""
    _ensure_db()
    with open(DB_PATH, "r") as f:
        return json.load(f)


def save_accounts(accounts: list[dict]) -> None:
    """Persist the full accounts list to the JSON database."""
    _ensure_db()
    with open(DB_PATH, "w") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


def get_account(phone: str) -> Optional[dict]:
    """Find and return a single account by phone number, or None."""
    for acc in load_accounts():
        if acc["phone"] == phone:
            return acc
    return None


def upsert_account(account: dict) -> None:
    """
    Insert a new account or update an existing one (matched by phone).
    """
    accounts = load_accounts()
    for i, acc in enumerate(accounts):
        if acc["phone"] == account["phone"]:
            accounts[i] = account
            save_accounts(accounts)
            return
    accounts.append(account)
    save_accounts(accounts)


def delete_account(phone: str) -> bool:
    """
    Remove an account by phone number.
    Returns True if it was found and deleted, False otherwise.
    """
    accounts = load_accounts()
    new_accounts = [acc for acc in accounts if acc["phone"] != phone]
    if len(new_accounts) == len(accounts):
        return False
    save_accounts(new_accounts)
    return True
