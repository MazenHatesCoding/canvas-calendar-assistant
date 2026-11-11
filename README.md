# Canvas to Google Calendar Assistant

Your instructors announce quizzes and deadlines in Canvas posts, and you find out the night
before. This assistant reads those posts for you, works out the dates, puts them in your
Google Calendar, and tells you on Telegram.

It is built for courses where dates are buried in announcement text ("the quiz will be
next week during your lab") and never entered as real Canvas due dates.

- **Free.** It runs on free plans of Vercel, Google Gemini and Telegram.
- **Yours.** You run your own copy with your own keys. Nobody else sees your courses.
- **No programming.** Setup is done in the browser and takes about 20 minutes.

## What it does

- **Reads Canvas every hour:** course announcements and inbox messages. It only reads.
- **Understands them with AI,** using your own timetable, so "during your tutorial next
  week" becomes a real date and time.
- **Keeps your calendar right:** adds events, moves them when a deadline changes, removes
  them when something is cancelled, and skips posts meant for other groups.
- **Tells you on Telegram:** a message for every change, and a morning overview of your
  next 7 days.
- **Takes notes from you:** message the bot "graph theory quiz next Monday in the
  tutorial" and it lands in your calendar.
- **Reminds you** to renew your Canvas access token before it expires.

It only keeps what you have to act on. Grades being posted, uploaded slides and general
remarks are dropped silently.

## Set up your own copy

You need a Google account, a Telegram account, and free [GitHub](https://github.com/signup)
and [Vercel](https://vercel.com/signup) accounts (sign up to Vercel with your GitHub
account). Do it on a computer, not a phone.

You will collect seven values, then paste them all in step 6. Open a note and save each
one there as you go. **Treat them like passwords:** never post them or send them to anyone.

### 1. Canvas

1. Your first value is your university's Canvas address, for example
   `https://canvas.youruni.edu`. Copy it from the address bar while Canvas is open, up to
   and not including the first single `/` after the name. This is **CANVAS_BASE_URL**.
2. In Canvas, open **Account → Settings** and scroll to **Approved Integrations**.
3. Press **+ New Access Token**. For Purpose write `Calendar assistant`. For the expiry
   date pick the latest date Canvas allows.
4. Press **Generate Token** and copy the long token straight away. Canvas shows it only
   once. This is **CANVAS_TOKEN**.

### 2. Gemini (the AI)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in
   with your Google account.
2. Press **Create API key** and copy the key. This is **GEMINI_API_KEY**.

### 3. Telegram bot

1. In Telegram, open a chat with [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Give it a name (anything) and a username that ends in `bot`, for example
   `sara_canvas_bot`.
3. BotFather replies with a token that looks like `123456789:AAH...`. This is
   **TELEGRAM_BOT_TOKEN**.

Do not message your new bot yet.

### 4. Google Calendar

1. Open the [connect page](https://canvas-calendar-assistant.vercel.app/connect.html) and
   follow its three steps. Google will show a "hasn't verified this app" warning and the
   sign-in ends on an error page. Both are expected, and the page explains what to do.
2. At the end it shows two values. Copy both: **GOOGLE_TOKEN_JSON** and **CRON_SECRET**.

The assistant can only touch the one calendar it creates, named "University". It cannot
see your other calendars.

### 5. Your timetable

Write your week in plain sentences. The AI uses it to turn "in your lab next week" into a
date, and to tell which posts are for your group. This is **TIMETABLE**. For example:

```
I am in group 2. My classes run Sunday to Wednesday. Thursday is a day off for me.
Sunday 09:20-12:10: Calculus 2 (MA102) Lecture, Dr. Example, room A-101.
Sunday 14:00-15:40: Calculus 2 (MA102) Tutorial, Eng. Example, room A-204.
Monday 11:20-13:00: Physics 1 (PH101) Lab, Eng. Example, room Lab 3.
Tuesday 09:20-12:10: Physics 1 (PH101) Lecture, Dr. Example, room A-101.
```

What matters: your group or section, and for every session the day, start and end time,
course, type (lecture, tutorial, lab) and room. Add anything that helps, such as "I am in
section B for labs".

### 6. Create your copy

1. Press this button:

   [![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2FMazenHatesCoding%2Fcanvas-to-google-calendar-assistant&project-name=canvas-to-google-calendar-assistant&repository-name=canvas-to-google-calendar-assistant&env=CANVAS_BASE_URL,CANVAS_TOKEN,GEMINI_API_KEY,TELEGRAM_BOT_TOKEN,GOOGLE_TOKEN_JSON,CRON_SECRET,TIMETABLE&envDescription=The%20seven%20values%20from%20steps%201%20to%205%20of%20the%20setup%20guide.&envLink=https%3A%2F%2Fgithub.com%2FMazenHatesCoding%2Fcanvas-to-google-calendar-assistant%23set-up-your-own-copy)

2. Vercel asks to create a repository in your GitHub account. Keep **private** selected
   and continue.
3. Paste your seven values into the matching boxes and press **Deploy**. Wait about a
   minute for it to finish. If the TIMETABLE box only takes one line, write your
   timetable as one long line. It works the same.
4. Vercel shows your copy's address, something like
   `https://canvas-to-google-calendar-assistant-abc123.vercel.app`. Save it.

### 7. Test and connect

1. Open your copy's address with `/setup.html` added to the end.
2. Paste your **CRON_SECRET** and press **Test and connect**.
3. Every line should show ✅. If one shows ❌, it tells you which value is wrong. See
   [Changing a value](#changing-a-value), then test again.
4. Open your bot in Telegram and press **Start**. The first person to message the bot
   becomes its owner, so do this right away.
5. Send `/check`. The assistant reads the last 21 days of Canvas posts and fills your
   calendar. The first time can take a few `/check` messages to get through everything.

That is all. It now runs on its own, every hour.

## Using the bot

| Send | What happens |
|---|---|
| `/week` | Your next 7 days |
| `/check` | Reads Canvas right now, without waiting for the hourly check |
| `graph theory quiz next Monday in the tutorial` | Adds it to your calendar |
| `the database quiz moved to Thursday at 2` | Changes the event |
| `cancel the physics lab on Tuesday` | Removes the event |
| `/help` | A short reminder of all this |

Events appear in the "University" calendar in Google Calendar, with reminders one day and
two hours before.

## Good to know

- **Check important dates yourself.** The AI can misread a date. Events it is unsure
  about are marked `[?]`, and every event links back to the post it came from.
- **The free AI allowance is small:** about 20 requests a day per Gemini model, and one
  request per new post or typed message. Normal use stays far below it. When one model
  runs out the assistant moves to the next, and posts it could not process wait for a
  later check. Nothing is lost.
- **The hourly check can start up to 15 minutes late.** That is how Vercel's free plan
  works.
- **Canvas tokens expire.** The bot reminds you a few days before. Create a new token as
  in step 1 and replace **CANVAS_TOKEN** as described below.
- **Outside Egypt?** Add a value named `TIMEZONE`, for example `Europe/London`. The
  default is `Africa/Cairo`.

### Changing a value

1. On [vercel.com](https://vercel.com), open your project, then **Settings → Environment
   Variables**.
2. Edit the value and save.
3. Open **Deployments**, press the three dots on the newest one, and choose **Redeploy**.
   A changed value only takes effect after a redeploy.

### If something is wrong

| Problem | What to do |
|---|---|
| The bot does not answer | Open `/setup.html` on your copy and run the test again. |
| "Canvas rejected the access token" | The token expired. Create a new one and replace **CANVAS_TOKEN**. |
| "The AI service is unavailable" | The free allowance is used up or Google is busy. Send it again later. |
| An event has the wrong date | Tell the bot, for example "the database quiz is on Thursday at 2, not Wednesday". |
| You want to stop | Delete the project on Vercel, delete the "University" calendar, and remove the app at [myaccount.google.com/permissions](https://myaccount.google.com/permissions). |

## Privacy

Your copy runs in your own Vercel account with your own keys. Each new post is sent,
together with your timetable, to Google's Gemini API to be understood. Events are saved
in your own Google Calendar. The connect page in step 4 passes your Google sign-in to
Google and hands the result back to you without keeping it.
[Privacy policy](https://canvas-calendar-assistant.vercel.app/privacy.html) ·
[Terms](https://canvas-calendar-assistant.vercel.app/terms.html)

## For developers

| Part | File |
|---|---|
| The assistant's logic | `assistant/core.py` |
| Canvas reading | `assistant/canvas.py` |
| AI decisions and the rules it follows | `assistant/extractor.py` |
| Google Calendar and the saved state | `assistant/gcal.py` |
| Telegram messages | `assistant/telegram.py` |
| Hourly check, bot replies, setup test, Google sign-in | `api/*.py` |
| Schedule and time limits | `vercel.json` |
| Site, connect page and setup page | `public/` |

There is no database. The assistant's small saved state lives in a hidden event on
1 January 2000 in its own calendar.

To run the Canvas check on your own computer:

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
copy .env.example .env                            # then fill it in
copy timetable.example.txt timetable.txt          # then describe your own week
.venv/Scripts/python -m assistant --verbose       # one Canvas check
.venv/Scripts/python -m unittest discover tests   # the tests
```

For Google access locally, either paste the connect page's value into `.env` as
`GOOGLE_TOKEN_JSON`, or use your own Google Cloud OAuth client (type "Desktop app", saved
as `credentials.json`) and run `python -m assistant.connect_google`.

Released under the [MIT License](LICENSE). An independent student project, not affiliated
with Instructure (Canvas), Google, Telegram or Vercel.
