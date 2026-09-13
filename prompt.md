[9/13/26 11:21 PM] pourya: I have an existing Telegram bot built with Kurigram (a Pyrogram fork) that acts as an account manager. It already handles adding accounts, listing them with pagination, deleting accounts, updating Star balances, and backing up the JSON database. All of that is done — do not modify or regenerate those features.

Now I want to build a new feature on top of it: automated reactions + Star-gifting to channel posts, with detailed logging sent to the admin for every critical event.

---

🎯 Goal

The bot monitors one specific Telegram channel (where the bot token is an admin) and, for every qualifying post, uses a random subset of the stored accounts to:

1. Send a random reaction from a predefined list, and
2. Send a random amount of Stars.

Every action and every failure must be logged and reported to the admin.

---

🧩 Context

· The bot stores multiple Telegram accounts (e.g., ~21) in its JSON database.
· Each stored account has: phone number, name, session string, Star balance, and TFA (if any).
· The bot's own token is an admin in the target channel.
· All actions target that one specific channel only.
· The admin must receive detailed logs for every event (success or failure).

---

🔄 Full Design Flow (end-to-end)

Step 1 — Detect a new post

· The bot monitors the target channel (defined in .env).
· When a new post appears, it captures the message ID and content.

Step 2 — Filter the post

· Check whether a keyword/phrase (defined in .env) appears in either:
  · The post's caption, or
  · The post's text.
· If the keyword is missing → skip the post entirely. Stop here.
· If the keyword is present → continue.

Step 3 — Wait 13 minutes

· The bot waits 13 minutes (configurable via .env, default 13) before doing anything.
· After the delay, it proceeds.

Step 4 — Select accounts

· Read from .env:
  · MIN_ACCOUNTS (e.g., 2)
  · MAX_ACCOUNTS (e.g., 5)
· Pick a random number between MIN_ACCOUNTS and MAX_ACCOUNTS.
· Randomly select that many accounts from the stored pool.

Step 5 — Validate balances

· Read from .env:
  · MIN_STARS (e.g., 3)
  · MAX_STARS (e.g., 5)
· Every selected account must have a Star balance greater than or equal to MAX_STARS (the maximum possible amount in the range — not just the amount it will send).
· If any selected account fails this check → exclude it and replace it with another eligible account.
· Keep re-selecting until the required count is filled, or fail gracefully and notify the admin if there aren't enough eligible accounts.

Step 6 — Assign random reactions

· Read a list of allowed reactions from .env (e.g., ❤️, 👍, 🔥, 🎉).
· For each selected account, pick a random reaction from that list.

Step 7 — Assign random Star amounts

· For each selected account, pick a random Star amount between MIN_STARS and MAX_STARS.

Step 8 — Perform actions (per account)

For each selected account:

1. Connect using its saved session.
2. Send the assigned reaction to the target post.
3. Send the assigned Star amount to the target post.
4. Handle errors per account (session revoked, insufficient balance, rate limit, flood wait, etc.) and log them.

Step 9 — Detailed logging to the admin (critical)

Every single action must be logged and sent to the admin in detail. This includes:

Per successful account action:

· Which account (phone number / name)
· How many Stars were sent
· Which post (include the post link)
· Which reaction was sent
· Timestamp

Example format:

✅ Account +98xxxxxxxxxx (Ali) sent 4 Stars and reacted ❤️ to post: [link]

Per failed account action:

· Which account
· What it was trying to do
· The exact error/reason (e.g., session revoked, flood wait, insufficient balance)
· The post link

Example format:

❌ Account +98xxxxxxxxxx failed to send Stars — Reason: session revoked. Post: [link]

When NOT enough eligible accounts exist:
[9/13/26 11:21 PM] pourya: · If the bot needs N accounts but cannot find enough with balance ≥ MAX_STARS, it must immediately notify the admin.
· The message must include:
  · How many accounts were needed
  · How many were available
  · That no Stars/reactions could be sent for that post
  · The post link

Example format:

⚠️ Could not process post [link] — needed 4 eligible accounts, but only 1 had balance ≥ 5 Stars. No Stars or reactions were sent.

Overall summary per post (optional but recommended):

· Post link
· Number of accounts selected
· Number of successful sends
· Number of failures
· Total Stars sent

---

⚙️ Configuration (.env) — everything must be easily settable here

Make all of the following tunable via .env, with sensible defaults:

Variable Description Example
TARGET_CHANNEL Channel ID or username to monitor @mychannel
POST_KEYWORD Keyword/phrase that must appear in the post caption or text giveaway
DELAY_MINUTES Minutes to wait after post detection before acting 13
MIN_ACCOUNTS Minimum number of accounts to select 2
MAX_ACCOUNTS Maximum number of accounts to select 5
MIN_STARS Minimum Stars to send per account 3
MAX_STARS Maximum Stars to send per account 5
REACTIONS Comma-separated list of allowed reactions ❤️,👍,🔥,🎉
ADMIN_ID Telegram user ID of the admin who receives all logs 123456789

---

🧱 Style Guidelines

· Keep the code modular, clean, and well-commented.
· Reuse the existing JSON database and session logic — do not rewrite them.
· Follow the same inline-keyboard / clean-code conventions used in the existing bot.
· The delay, keyword filter, account ranges, Star ranges, and reaction list must all be configurable via .env with sensible defaults.
· All logs must be sent to the admin — no silent failures.

---

✅ Summary

For every new post in the target channel:

· If it contains the keyword (in caption or text),
· Wait DELAY_MINUTES (default 13),
· Randomly select N accounts (between MIN_ACCOUNTS and MAX_ACCOUNTS),
· Ensure each has ≥ MAX_STARS Stars,
· Send a random reaction from the REACTIONS list,
· Send a random Star amount between MIN_STARS and MAX_STARS,
· Log every single action (success, failure, and "not enough accounts") and send all logs to the admin with full details and post links.

---

Please generate the full working code for this feature, integrated cleanly with the existing bot.

---

This prompt is now 100% complete and ready to hand off. ✅

Quick confirmations before you send it:

1. Star balance check — >= MAX_STARS or strictly > MAX_STARS?
2. Reaction order — reaction first, then Stars, or does order not matter?
3. Should the per-post summary be sent to the admin as well, or is per-account logging enough?