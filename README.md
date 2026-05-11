# HSE Alumni Telegram Bot — St. Petersburg

A Telegram bot that helps HSE St. Petersburg alumni check the status of their alumni card and receive notifications when it's ready. All data is stored in a Google Spreadsheet managed by the alumni relations team.

---

## Features

**For alumni:**
- Check card status by full name and date of birth
- Subscribe to card-ready notifications or all community updates
- Check card validity period by card number
- Browse FAQ and contact information

**For admins:**
- Send broadcast messages to all subscribers or filter by educational program

---

## How it works

### Card status check

The user enters their last name, first name, patronymic, and date of birth. The bot looks up the match in the main sheet (`выпуск карт - запросы`) and returns the current value from the `статус` column. Once found, the user's Telegram ID is saved to the `Telegram ID` column so they don't need to re-enter their details next time.

Possible statuses:

| Value in the spreadsheet | What the user sees |
|---|---|
| `напечатано` | Card is printed and ready for pickup |
| `ушло в печать` | Card is being printed |
| `проблема с фото` | Photo didn't meet requirements, a new one is needed |
| `новое фото!` | New photo received, card sent to print |
| `уточнение статуса` | User needs to contact the office |
| `ждем приказа` | Current-year graduate, application available in autumn |
| `блокировка` | Card is blocked |
| `уже есть пропуск` | A previously issued pass is on record |
| `ст.магистр` | User is currently a master's student |

### Card validity check

The user enters their card number. The bot looks it up in the `Продление доступов` sheet, column `Номер карты`. If the card appears more than once (multiple renewals), the entry with the latest date in `Продление ДО` is returned.

### Subscriptions

When a user subscribes, the bot adds or updates their record in the `подписчики` sheet (created automatically on first subscription). Stored fields: Telegram ID, @username, subscription date, and subscription type — `all` (all updates) or `ready_only` (card-ready notifications only).

### Automatic notifications

Every day at 10:00 the bot checks whether the status has changed for users with a linked Telegram ID. If the status has switched to one of the tracked values (`напечатано`, `новое фото!`, `уточнение статуса`, `проблема с фото`) — a personal message is sent.

On the 1st of each month at 12:00 the bot sends a reminder to all subscribers whose status is `напечатано` and who haven't received this notification yet (tracked via the `Последнее уведомление «напечатано»` column in `подписчики`).

### Admin broadcast

Users listed in the `admins` sheet see an extra "📢 Рассылка сообщения" button in the menu. They can send a message (text or photo) to all subscribers with type `all`, or filter by educational program from the `Образовательная программа/ Специальность` column.

---

## Google Spreadsheet structure

The bot uses a single Google Spreadsheet with the following sheets.

### Sheet `выпуск карт - запросы` (main)

The primary database. Sheet name is configurable via `WORKSHEET_NAME`.

| Column | Contents |
|---|---|
| `Фамилия` | Last name |
| `Имя` | First name |
| `Отчество` | Patronymic |
| `Дата рождения` | Date of birth, format `ДД.ММ.ГГГГ` |
| `статус` | Current card status (see table above) |
| `Образовательная программа/ Специальность` | Used for broadcast filtering |
| `Telegram ID` | Filled in automatically by the bot on first lookup |

### Sheet `Продление доступов`

Used for card validity checks.

| Column | Contents |
|---|---|
| `Номер карты` | Card number |
| `Продление ДО` | Validity expiry date, format `ДД.ММ.ГГГГ` |

### Sheet `подписчики`

Created automatically on first subscription.

| Column | Contents |
|---|---|
| `Telegram ID` | User's Telegram ID |
| `@username` | Telegram username (if set) |
| `Дата подписки` | Subscription date and time |
| `Тип подписки` | `all` or `ready_only` |
| `Последнее уведомление «напечатано»` | Date of last monthly card-ready notification |

### Sheet `admins`

Create manually. One column — `Telegram username` — with admin usernames listed without `@`. Only users in this list see the broadcast button.

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure the bot

Open `hse_alumni_bot.py` and fill in the configuration block at the top of the file:

```python
SPREADSHEET_ID = ""                        # your spreadsheet ID
WORKSHEET_NAME = "выпуск карт - запросы"  # main sheet name
CHECK_TIME = time(10, 0)                   # daily notification check time
```

Set the bot token as an environment variable (get it from [@BotFather](https://t.me/BotFather)):

```bash
export BOT_TOKEN=your_token_here   # Linux / macOS
set BOT_TOKEN=your_token_here      # Windows
```

### 4. Add Google Cloud credentials

Place `credentials.json` (a Google Cloud service account key) in the project root. **This file must not be committed to the repository.**

How to get it:
1. Open [Google Cloud Console](https://console.cloud.google.com/) → create a project
2. Enable **Google Sheets API** and **Google Drive API**
3. Create a service account → download the JSON key → rename it to `credentials.json`
4. Open your Google Spreadsheet → share it with the service account email (role: **Editor**)

### 5. Run

```bash
python hse_alumni_bot.py
```

---
