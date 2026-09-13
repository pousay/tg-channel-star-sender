Here's the complete, consolidated prompt with everything we've discussed so far:

---

Prompt:

I want you to write a Telegram bot using Kurigram (a fork of Pyrogram). The bot should function as an account manager / account receiver for the admin.

---

General

· All user interaction must use inline keyboard buttons — never reply keyboards.
· The JSON file acts as the database. No encryption needed — store everything in plain text.
· Keep the code clean, modular, and well-commented.
· Store the following per account in the JSON file:
  · Phone number
  · Name
  · Star balance
  · Session string
  · TFA (2FA) password/code, if required

---

1. /start command

Displays inline keyboard buttons:

· Add Account
· List Accounts
· Update Stars Balance

---

2. "Add Account" flow

· Bot asks for a phone number.
· Bot attempts to log in via Telegram.
· Telegram sends a login code; bot asks the admin to enter it.
· Bot checks the code:
  · Valid → proceed.
  · Invalid → inform the admin.
· If Two-Factor Authentication (2FA/TFA) is required, bot asks for the TFA password, verifies it, and completes login.
· After successful login:
  · Retrieve the account's Star balance.
  · Save the session string, phone number, name, Star balance, and TFA password (if required) to the JSON file.

---

3. "List Accounts" flow

· Lists all saved accounts with pagination (e.g., 20 accounts per page).
· Show Next page / Previous page buttons and a label like Page X of Y.
· For each account display:
  · Phone number (must be copyable — use <code> formatting in the message)
  · Name
  · Star balance
  · Any extra relevant info
· Provide a Delete Account button and a Back button.

---

4. "Delete Account" flow

· When the admin taps Delete Account, the bot asks for the phone number of the account to delete.
· Bot validates:
  · Whether the phone number format is correct.
  · Whether that phone number exists in the JSON file.
· If valid and found, bot asks for approval: Are you sure you want to delete this account?
  · Buttons: Yes / Cancel.
· If the admin taps Yes, the account is removed from the JSON file and a confirmation is shown.

---

5. "Update Stars Balance" flow

· When the admin taps Update Stars Balance, the bot walks through every saved account, tries to connect using the saved session, and updates the Star balance in the JSON file.
· If an account cannot be accessed (e.g., session revoked, connection failure, etc.), the bot must notify the admin with:
  · Which account failed
  · The reason for the failure

---

6. Secret /backup command

· A hidden command /backup that sends the JSON file (containing all accounts) directly to the admin as a document.

---

Style guidelines

· All user interaction must use inline keyboard buttons, never reply keyboards.
· Keep the code clean, modular, and well-commented.
· The JSON file acts as the database.

Please generate the full working code.

---

Want me to now actually generate the code for this? If so, please confirm:

1. Admin restriction — should only a specific admin user IDs (not ID but IDs)
2. Kurigram import — you must do this: from pyrogram import Client (since Kurigram is a drop-in fork).
3. Star balance method — you can check kurigram docs for this option



use .env
generate .env.example
clean folder structure
make branches, do commits, do not merge any branch, open PRs using gh command but do not close any, I will do that by hand, make issues and solve them