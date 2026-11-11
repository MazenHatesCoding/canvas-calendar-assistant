# Canvas Calendar Assistant

Reads the announcements and messages your instructors post on Canvas, works out which ones
are things you have to do or attend, and puts them in your Google Calendar. A Telegram bot
tells you what changed.

It is built for courses where dates are buried in announcement text ("the quiz will be
next week during your lab") and never entered as real Canvas due dates.

> **Status:** works for its author. A step-by-step setup guide for students who do not
> program is not written yet; the notes below assume you are comfortable with a terminal.

## What it does

- **Reads Canvas** once an hour: course announcements and inbox messages. Read-only.
- **Understands them with AI** (Google Gemini, free tier), using your own timetable, so
  "during your tutorial next week" becomes a real date and time.
- **Keeps your calendar right**: creates events, moves them when a deadline is extended,
  removes them when something is cancelled, and ignores posts meant for other groups.
- **Tells you on Telegram**: a message for each change, and a morning overview of the next 7 days.
- **Takes notes from you**: message the bot "graph theory quiz next Monday in the tutorial"
  and it lands in your calendar. Send `/week` for the 7-day overview. Replies are instant.
- **Reminds you** to renew your Canvas access token before it expires.

It only keeps what you must act on. Grades being posted, uploaded slides and general
remarks are dropped silently.

## How it runs

Your copy is hosted on Vercel's free plan, with your own keys stored as the project's
Environment Variables. Vercel starts the Canvas check every hour, and Telegram calls your
copy the moment you message the bot. The events live in a calendar the assistant creates
in your Google account; it has no access to your other calendars.

| Part | File |
|---|---|
| The assistant's logic | `assistant/core.py` |
| Canvas reading | `assistant/canvas.py` |
| AI decisions and the rules it follows | `assistant/extractor.py` |
| Google Calendar and the saved state | `assistant/gcal.py` |
| Telegram messages | `assistant/telegram.py` |
| Hourly check (web address Vercel calls) | `api/check.py` |
| Bot replies (web address Telegram calls) | `api/telegram.py` |
| Schedule and time limits | `vercel.json` |
| Home, privacy and terms pages | `public/` |

## Running it yourself

You need a Canvas access token, a Gemini API key, a Telegram bot token and a Google Cloud
OAuth client (type "Desktop app", saved as `credentials.json`).

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
copy .env.example .env                            # then fill it in
copy timetable.example.txt timetable.txt          # then describe your own week
.venv/Scripts/python -m assistant.connect_google  # one-time Google sign-in
.venv/Scripts/python -m assistant --verbose       # one Canvas check, on your computer
```

To host it on Vercel:

1. Create a Vercel project from this folder (`npx vercel link`).
2. Add every value from `.env` as an Environment Variable with the same name, plus
   `GOOGLE_TOKEN_JSON` (the contents of `token.json`) and `TIMETABLE` (the contents of
   `timetable.txt`). `CRON_SECRET` and `TELEGRAM_WEBHOOK_SECRET` are two long random
   strings you make up; they keep strangers from calling your copy.
3. Deploy: `npx vercel deploy --prod`. Deploy again after changing any variable.
4. Connect the bot: `.venv/Scripts/python -m assistant.connect_telegram https://<your-project>.vercel.app`
5. Send your bot any message. The first person to message it becomes its owner.

## Good to know

- **Check important dates yourself.** The AI can misread a date. Uncertain events are
  marked `[?]`, and every event links back to the post it came from.
- **The free Gemini tier is small** (about 20 requests a day per model). Normal use stays
  far below it; the assistant moves to the next model when one runs out.
- **Canvas tokens expire.** When yours does, replace the `CANVAS_TOKEN` and
  `CANVAS_TOKEN_CREATED` variables on Vercel and deploy again.
- **The hourly check can start up to 15 minutes late.** That is how Vercel's free plan
  schedules work.

Tests: `python -m unittest discover tests`

Not affiliated with Instructure (Canvas), Google or Telegram.
[Privacy policy](https://canvas-calendar-assistant.vercel.app/privacy.html) ·
[Terms](https://canvas-calendar-assistant.vercel.app/terms.html)
