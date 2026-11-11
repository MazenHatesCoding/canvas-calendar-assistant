"""One run of the assistant. Meant to be started about once an hour:  python -m assistant

1. Reads what the student sent the Telegram bot (button presses, requests, things to add by hand).
2. Fetches new Canvas items, lets the AI decide what each one means, and updates Google Calendar.
3. Sends the Canvas token reminder and the morning digest when they are due.

Add --verbose to print titles and reasons. Without it the log holds no course content,
so it is safe when the run log is public.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from . import config, telegram as tg
from .canvas import CanvasClient, CanvasProblem, Item
from .config import setting
from .extractor import AIUnavailable, Extractor, Unreadable
from .gcal import Calendar, describe, private

DEFAULT_MODELS = "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite"
REQUIRED = ("CANVAS_BASE_URL", "CANVAS_TOKEN", "GEMINI_API_KEY", "GOOGLE_CALENDAR_ID")
REMEMBERED_IDS = 400  # keeps the saved state well inside what one event description holds
TOKEN_LIFETIME_DAYS = 30
TOKEN_REMIND_AFTER_DAYS = 25
QUIET_BEFORE_HOUR = 8  # the token reminder waits until this hour, local time
NOTICE_MAX_AGE = timedelta(hours=48)


def short_id(item_id):
    """ "announcement-11733" -> "a11733": the form in which finished items are remembered."""
    kind, _, number = item_id.partition("-")
    return kind[:1] + number


class Assistant:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.tz_name = setting("TIMEZONE", "Africa/Cairo")
        self.tz = ZoneInfo(self.tz_name)
        self.now = datetime.now(self.tz)
        self.today = self.now.date().isoformat()
        self.lookback_days = int(setting("LOOKBACK_DAYS", "21"))

        self.calendar = Calendar(setting("GOOGLE_CALENDAR_ID"), self.tz_name)
        self.state = self.calendar.load_state()
        # Events the AI may update: everything from the lookback window onwards.
        self.events = self.calendar.events_since(self.now - timedelta(days=self.lookback_days + 1))
        self.extractor = Extractor(
            setting("GEMINI_API_KEY"),
            setting("GEMINI_MODELS", DEFAULT_MODELS).split(","),
            self.tz_name,
            config.timetable(),
        )
        token = setting("TELEGRAM_BOT_TOKEN")
        self.telegram = tg.Telegram(token) if token else None
        self.week_requested = False

    # --- talking to the student ---

    @property
    def chat_id(self):
        fixed = setting("TELEGRAM_CHAT_ID")
        return int(fixed) if fixed else self.state.get("chat_id")

    def tell(self, text, buttons=None):
        """Send a Telegram message. Returns False if it could not be sent."""
        if not self.telegram or not self.chat_id:
            return False
        try:
            self.telegram.send(self.chat_id, text, buttons)
            return True
        except RuntimeError as error:
            print(error)
            return False

    def alert(self, key, text):
        """A problem report, sent at most once a day per kind of problem."""
        alerts = self.state.setdefault("alerts", {})
        if alerts.get(key) != self.today and self.tell(f"⚠️ {escape(text)}"):
            alerts[key] = self.today

    # --- what the student sent ---

    def handle_telegram(self):
        if not self.telegram:
            return
        try:
            if not self.state.get("commands_set"):
                self.telegram.set_commands()  # fills the bot's "/" menu, once
                self.state["commands_set"] = True
            updates = self.telegram.updates(self.state.get("tg_offset", 0))
        except RuntimeError as error:
            print(error)
            return
        for update in updates:
            self.state["tg_offset"] = update["update_id"] + 1
            if "callback_query" in update:
                self.handle_button(update["callback_query"])
                continue
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            chat = message.get("chat") or {}
            if not text or chat.get("type") != "private":
                continue
            if not self.chat_id:
                self.link_chat(chat["id"])
            elif chat["id"] != self.chat_id:
                pass  # someone else found the bot: ignore them
            elif tg.wants_week(text):
                self.week_requested = True  # answered after Canvas is read, so it is up to date
            elif text.startswith("/"):
                self.tell(tg.HELP)
            else:
                self.add_by_hand(update["update_id"], message)

    def link_chat(self, chat_id):
        """The first person to message the bot becomes its owner."""
        self.state["chat_id"] = chat_id
        self.tell("✅ Linked. I'll send your notifications here.\n\n" + tg.HELP)
        print("telegram: chat linked")

    def add_by_hand(self, update_id, message):
        item = Item(
            id=f"manual-{update_id}",
            kind="note typed by the student",
            course="",
            title="",
            body=message["text"],
            posted_at=datetime.fromtimestamp(message["date"], timezone.utc),
            url="",
        )
        try:
            outcome, text, _ = self.apply(item, manual=True)
        except AIUnavailable as error:
            print(f"{item.id}: {error}")
            self.tell("⚠️ The AI service is unavailable right now. Please send that again later.")
            return
        except Unreadable:
            self.tell("⚠️ I couldn't make sense of that. Could you put it another way?")
            return
        except RuntimeError as error:  # Google Calendar refused the change
            print(f"{item.id}: {error}")
            self.tell("⚠️ I couldn't save that to your calendar. Please send it again later.")
            return
        print(f"{item.id}: {outcome}")
        self.tell(text if outcome != "ignored" else f"I didn't add anything. {text}")

    def handle_button(self, query):
        message = query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        try:
            self.telegram.answer_button(query["id"])
        except RuntimeError:
            pass  # a press older than a few minutes can no longer be acknowledged
        if not chat_id or chat_id != self.chat_id:
            return
        try:
            self.telegram.clear_buttons(chat_id, message["message_id"])
        except RuntimeError as error:
            print(error)
        if query.get("data") == "token_done":
            self.state.setdefault("token", {})["done"] = True
            self.tell(
                "✅ Marked as done. I won't remind you about this token again.\n\n"
                "When you save the new token, also set CANVAS_TOKEN_CREATED to the day you "
                "created it, so the next reminder comes on time."
            )
        elif query.get("data") == "token_snooze":
            self.tell("💤 OK. I'll remind you again tomorrow.")

    # --- deciding and writing ---

    def apply(self, item, manual=False):
        """Returns (outcome, message for the student, whether it is worth sending unprompted)."""
        posted_day = item.posted_at.astimezone(self.tz).date()
        known = [{"key": f"E{n}", **describe(event)} for n, event in enumerate(self.events, 1)]
        decision = self.extractor.decide(item, known, manual)
        if self.verbose:
            print(f"   {item.course} | {item.title} | {decision.action} | {decision.title} | "
                  f"{decision.start} | {decision.reason}")

        target = None
        if decision.action in ("update", "note"):
            key = decision.target_key or ""
            index = int(key[1:]) - 1 if key[:1] == "E" and key[1:].isdigit() else -1
            if 0 <= index < len(self.events):
                target = index

        if decision.action == "create":
            event = self.calendar.create(decision, item, posted_day)
            self.events.append(event)
            return "created", tg.added_text(event, decision, item, self.tz), self.is_ahead(event)

        if target is not None:
            old = self.events[target]
            if decision.action == "update" and decision.cancelled:
                self.calendar.delete(old)
                self.events.pop(target)
                return "cancelled", tg.cancelled_text(old, item, self.tz), self.is_ahead(old)
            event = self.calendar.update(old, decision, item, posted_day)
            self.events[target] = event
            if decision.action == "update":
                return "updated", tg.changed_text(event, old, item, self.tz), self.is_ahead(event)
            return "noted", tg.noted_text(event, decision, item, self.tz), self.is_ahead(event)

        if decision.action == "notify":
            fresh = self.now - item.posted_at <= NOTICE_MAX_AGE
            return "notified", tg.notice_text(decision, item), fresh

        return "ignored", escape(decision.reason), False

    def is_ahead(self, event):
        return tg.ends_after(event, self.now, self.tz)

    def handle_canvas(self):
        client = CanvasClient(setting("CANVAS_BASE_URL"), setting("CANVAS_TOKEN"))
        recent = client.recent_items(self.lookback_days)

        # Remember finished items only while Canvas still returns them, so the list stays small.
        in_window = {short_id(item.id) for item in recent}
        done = [i for i in self.state.get("done", []) if i in in_window][-REMEMBERED_IDS:]
        self.state["done"] = done
        # Safety net if the saved state is ever lost: an item that already has an event is finished.
        have_event = {private(event).get("source") for event in self.events}
        done.extend(
            short_id(item.id)
            for item in recent
            if item.id in have_event and short_id(item.id) not in done
        )

        items = [item for item in recent if short_id(item.id) not in done]
        print(f"{len(items)} new Canvas item(s).")
        for item in items:
            try:
                outcome, text, worth_sending = self.apply(item)
            except AIUnavailable as error:
                # Leave this item and the rest for the next run.
                print(f"Stopping early: {error}.")
                if not error.quota:
                    self.alert("ai", f"{error}. Check GEMINI_API_KEY. I will keep retrying.")
                return
            except Unreadable:
                # Retrying would fail the same way, so hand it to the student instead.
                outcome, worth_sending = "unreadable", True
                text = (
                    f"⚠️ I couldn't work out this Canvas post: <b>{escape(item.title)}</b>. "
                    f'Please read it yourself.\n<a href="{escape(item.url)}">Open in Canvas</a>'
                )
            done.append(short_id(item.id))
            print(f"{item.id}: {outcome}")
            if worth_sending:
                self.tell(text)

    # --- scheduled and requested messages ---

    def token_reminder(self):
        created = setting("CANVAS_TOKEN_CREATED")
        try:
            created_day = date.fromisoformat(created)
        except ValueError:
            if created:
                print("CANVAS_TOKEN_CREATED is not a date like 2026-11-11: no token reminder.")
            return
        token = self.state.setdefault("token", {})
        if token.get("created") != created:  # a new token was saved: start over
            token.clear()
            token["created"] = created
        age = (self.now.date() - created_day).days
        if (
            token.get("done")
            or age < TOKEN_REMIND_AFTER_DAYS
            or self.now.hour < QUIET_BEFORE_HOUR
            or token.get("sent") == self.today
        ):
            return
        left = TOKEN_LIFETIME_DAYS - age
        when = f"expires in {left} day{'s' if left != 1 else ''}" if left > 0 else "has probably expired"
        sent = self.tell(
            f"🔑 <b>Your Canvas access token {when}.</b>\n"
            "Create a new one in Canvas (Account → Settings → New Access Token) and save it "
            "as CANVAS_TOKEN, or I will stop seeing your announcements.",
            buttons=[("Snooze until tomorrow", "token_snooze"), ("Done", "token_done")],
        )
        if sent:
            token["sent"] = self.today
            print("token reminder sent")

    def week_overview(self):
        """Answer a /week request."""
        text = tg.digest_text(self.events, self.now, self.tz)
        self.tell(text or "Nothing in your calendar for the next 7 days. 🎉")
        print("week overview sent")

    def morning_digest(self):
        hour = setting("DIGEST_HOUR", "7")
        if not hour.isdigit() or self.now.hour < int(hour) or self.state.get("digest") == self.today:
            return
        self.state["digest"] = self.today
        text = tg.digest_text(self.events, self.now, self.tz)
        if text and not self.week_requested:  # a /week answer in this run already covers it
            self.tell(text)
            print("digest sent")

    def run(self):
        """Returns False if Canvas could not be read and the student could not be told."""
        told = True
        try:
            self.handle_telegram()
            try:
                self.handle_canvas()
                self.state.get("alerts", {}).pop("canvas", None)
            except CanvasProblem as problem:
                print(problem)
                self.alert("canvas", str(problem))
                told = self.state.get("alerts", {}).get("canvas") == self.today
            self.token_reminder()
            if self.week_requested:
                self.week_overview()
            self.morning_digest()
        finally:
            # Saved even if something fails, so finished items are never processed twice.
            self.calendar.save_state(self.state)
        return told


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles choke on Arabic otherwise
    config.load()
    missing = [name for name in REQUIRED if not setting(name)]
    if missing:
        sys.exit("Missing settings: " + ", ".join(missing))
    if not Assistant("--verbose" in sys.argv).run():
        sys.exit(1)


if __name__ == "__main__":
    main()
