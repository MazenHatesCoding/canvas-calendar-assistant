# Canvas Calendar Assistant

Reads the announcements and messages your instructors post on Canvas, works out which ones
are things you have to do or attend, and puts them in your Google Calendar. A Telegram bot
tells you what changed.

It is built for courses where dates are buried in announcement text ("the quiz will be
next week during your lab") and never entered as real Canvas due dates.

> **Status:** works for its author. A step-by-step setup guide for students who do not
> program is not written yet; the notes below assume you are comfortable with a terminal.

## What it does

- **Reads Canvas** about once an hour: course announcements and inbox messages. Read-only.
- **Understands them with AI** (Google Gemini, free tier), using your own timetable, so
  "during your tutorial next week" becomes a real date and time.
- **Keeps your calendar right**: creates events, moves them when a deadline is extended,
  removes them when something is cancelled, and ignores posts meant for other groups.
- **Tells you on Telegram**: a message for each change, and a morning overview of the next 7 days.
- **Takes notes from you**: message the bot "graph theory quiz next Monday in the tutorial"
  and it lands in your calendar. Send `/week` for the 7-day overview.
- **Reminds you** to renew your Canvas access token before it expires.

It only keeps what you must act on. Grades being posted, uploaded slides and general
remarks are dropped silently.

## How it runs

There is no server. Your copy runs on GitHub Actions on a schedule, with your own keys
stored as repository Secrets. The events live in a calendar the assistant creates in your
Google account; it has no access to your other calendars.

| Part | File |
|---|---|
| One hourly run | `assistant/__main__.py` |
| Canvas reading | `assistant/canvas.py` |
| AI decisions and the rules it follows | `assistant/extractor.py` |
| Google Calendar | `assistant/gcal.py` |
| Telegram messages | `assistant/telegram.py` |
| Schedule | `.github/workflows/assistant.yml` |

## Running it yourself

You need a Canvas access token, a Gemini API key, a Telegram bot token and a Google Cloud
OAuth client (type "Desktop app", saved as `credentials.json`).

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
copy .env.example .env                            # then fill it in
copy timetable.example.txt timetable.txt          # then describe your own week
.venv/Scripts/python -m assistant.connect_google  # one-time Google sign-in
.venv/Scripts/python -m assistant --verbose       # one run
```

Send your bot any message before the first run; the first person to message it becomes
its owner.

To run it hourly on GitHub, add every value from `.env` as a repository Secret with the
same name, plus `GOOGLE_TOKEN_JSON` (the contents of `token.json`) and `TIMETABLE` (the
contents of `timetable.txt`).

## Good to know

- **Check important dates yourself.** The AI can misread a date. Uncertain events are
  marked `[?]`, and every event links back to the post it came from.
- **Replies take up to an hour**, because the bot is only read when a run starts.
- **The free Gemini tier is small** (about 20 requests a day per model). Normal use stays
  far below it; the assistant moves to the next model when one runs out.
- **Canvas tokens expire.** When yours does, replace the `CANVAS_TOKEN` and
  `CANVAS_TOKEN_CREATED` secrets.

Tests: `python -m unittest discover tests`

Not affiliated with Instructure (Canvas), Google or Telegram.
[Privacy policy](https://canvas-calendar-assistant.vercel.app/privacy.html) ·
[Terms](https://canvas-calendar-assistant.vercel.app/terms.html)
