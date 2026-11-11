# TLU Schedule Bot for Telegram

A Telegram bot that sends your **class timetable, exam schedule and tuition fees** from the
Thuy Loi University student portal ([sinhvien1.tlu.edu.vn](https://sinhvien1.tlu.edu.vn)).
It runs on **GitHub Actions** every day, so you don't need a server.

The bot calls the portal's JSON API directly (the same requests the website makes), so it doesn't
need a browser and each run finishes in a few seconds.



## Features

Each run sends a message for the **target day**. When the bot runs in the evening, the target day is
tomorrow; when it runs in the morning, the target day is today.

| What | When |
|---|---|
| 🖼️ **Weekly timetable image**: a grid with Monday to Sunday as columns and periods 1 to 15 as rows. Each cell shows the subject and room, and the target day's column is highlighted | Every run |
| ☀️ / 🌙 **Day schedule**: time, periods, subject, room and lecturer for the target day, or a "day off" message | Every run |
| 🚨 **New, changed or removed exams**, compared with what was reported last time | As soon as the portal publishes them |
| ⏰ **Exam reminder** for the day after the target day | One day before |
| 📝 **Upcoming exams list** | Mondays |
| 💰 **Tuition fees**: the amount still owed | When the amount changes, plus a reminder every Monday while you still owe money |

If the portal is down, the bot sends the schedule saved from the last successful run and says so
in the message. Errors are reported to the same chat with a link to the run log.

## Setup

1. **Fork** this repository (or use your own copy of it).
2. **Create a Telegram bot** with [@BotFather](https://t.me/BotFather) and copy its token.
3. **Find your chat ID**: send any message to your bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`.
4. In your repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

   | Secret | Value |
   |---|---|
   | `TELE_BOT_TOKEN` | The bot token from BotFather |
   | `TELE_CHAT_ID` | Your chat ID |
   | `MSV` | Your student ID |
   | `PASS_TRUONG` | Your student portal password |

5. Open the **Actions** tab and enable workflows (forks have them disabled by default).
6. Run it once by hand: **Actions → Telegram Schedule Bot → Run workflow**.

## Changing the send time

The schedule is the `cron` line in [`.github/workflows/run_bot.yml`](.github/workflows/run_bot.yml).
Cron times are in **UTC**; Vietnam time is **UTC+7**.

```yaml
- cron: '17 10 * * *'   # 10:17 UTC = 17:17 Vietnam time → sends tomorrow's schedule
```

| Vietnam time | Cron (UTC) |
|---|---|
| 17:17 | `'17 10 * * *'` |
| 19:17 | `'17 12 * * *'` |
| 21:17 | `'17 14 * * *'` |

The bot picks the target day from the **actual** time it runs, so you only need to change the cron line:

- From 12:00 noon Vietnam time onward → **tomorrow's** schedule
- Before noon → **today's** schedule

Two things to keep in mind when choosing a time:

- **GitHub Actions often starts scheduled runs late.** Delays of 10 minutes to several hours are normal,
  and they are worst at the top of the hour. Choose an odd minute like `:17`, and schedule earlier than
  you want to receive the message.
- **The student portal stops responding late at night** (from around 1 AM). A run that lands then will
  fail to log in, and the bot falls back to the saved schedule.

## Manual runs

Use **Actions → Telegram Schedule Bot → Run workflow** to choose:

| Input | Options |
|---|---|
| `che_do` (mode) | `auto`: the Monday extras only on Mondays · `tuan`: force the Monday version · `ngay`: force the normal-day version |
| `ngay_bao` (target day) | `auto`: based on the time of day · `homnay`: today · `ngaymai`: tomorrow |

## Running locally

```bash
pip install -r requirements.txt
export TELE_BOT_TOKEN=... TELE_CHAT_ID=... MSV=... PASS_TRUONG=...
python bot_telegram.py                  # auto mode, auto target day
python bot_telegram.py ngay ngaymai     # normal-day version, tomorrow's schedule
```

Optional environment variables:

- `BOT_STATE_DIR`: where state is stored (default `.bot_state`)
- `TLU_BASE_URL`: use a different portal URL (for testing)

## How it works

```
bot_telegram.py              The bot: portal API client, message and image builders, Telegram sender
fonts/                       DejaVu Sans, so Vietnamese text renders correctly in the timetable image
.github/workflows/run_bot.yml  Daily schedule and manual-run inputs
docs/preview.png             The sample image above
```

- **State between runs**: the last saved schedule, exams that were already reported and the last
  tuition amount are kept in `.bot_state/`. The workflow saves and restores it with `actions/cache`.
- **SSL**: the portal's server doesn't send its intermediate certificate, so Python rejects the
  connection. Instead of turning off verification, the bot downloads the missing certificate from the
  address listed in the server's certificate, checks the whole chain against trusted root certificates,
  and only then connects. Your password never goes over an unverified connection.
- **Privacy**: GitHub Actions logs in a public repo are public. The bot never prints your password or
  tuition amounts to the log; those only go to your Telegram chat.
- **Account safety**: on a wrong password, the bot stops immediately and does not retry, so your
  portal account won't get locked.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Read timed out` / `HTTP 502` when logging in | The portal is down (common late at night). Run it again during the day, or move the cron earlier. |
| "Sai MSV hoặc mật khẩu" (wrong student ID or password) | Update the `PASS_TRUONG` secret. |
| No message arrives at all | Check `TELE_CHAT_ID`, and make sure you have sent at least one message to the bot. |
| Messages arrive hours late | That's GitHub Actions scheduling delay. Move the cron earlier. |
| `refusing to allow an OAuth App to ... update workflow` when pushing | Your GitHub CLI login is missing the `workflow` scope. Run `gh auth refresh -h github.com -s workflow`. |
