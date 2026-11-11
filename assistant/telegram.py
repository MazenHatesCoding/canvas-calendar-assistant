"""Telegram: sending messages, the webhook setup, and the message wording."""

import re
from datetime import datetime, timedelta
from html import escape

import requests

from .gcal import private, title_of

NOTE_LIMIT = 700  # Telegram rejects messages over 4096 characters
DEADLINE_KINDS = ("assignment", "project")
WEEK_REQUEST = re.compile(r"^\W*(week|wrap\s*-?\s*up|digest|next\s+(7|seven)\s+days|7\s+days)\W*$", re.I)

CHECK_REQUEST = re.compile(r"^\W*(check|refresh)(\s+(canvas|now|canvas\s+now))?\W*$", re.I)

COMMANDS = [
    {"command": "week", "description": "What is coming in the next 7 days"},
    {"command": "check", "description": "Check Canvas for new posts right now"},
    {"command": "help", "description": "How to add or change things"},
]

HELP = (
    "I watch your Canvas courses and put quizzes, deadlines and schedule changes in your "
    "calendar.\n\n"
    "To add something yourself, tell me in plain words, for example:\n"
    "<i>graph theory quiz next Monday in the tutorial, chapters 6 and 7</i>\n\n"
    "You can change or cancel something the same way:\n"
    "<i>the database quiz moved to Thursday at 2</i>\n\n"
    "Send /week to see your next 7 days.\n"
    "I read Canvas every hour. Send /check to make me read it right now."
)


class Telegram:
    def __init__(self, token):
        self._base = f"https://api.telegram.org/bot{token}"

    def _call(self, method, **payload):
        # Errors are rebuilt without the URL, because the URL contains the bot token.
        try:
            data = requests.post(f"{self._base}/{method}", json=payload, timeout=30).json()
        except (requests.RequestException, ValueError) as error:
            raise RuntimeError(f"Telegram {method} failed: {type(error).__name__}") from None
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data["result"]

    def set_webhook(self, url, secret):
        """Have Telegram deliver messages and button presses to `url`, signed with `secret`."""
        self._call(
            "setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"]
        )

    def webhook_info(self):
        return self._call("getWebhookInfo")

    def send(self, chat_id, text, buttons=None):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]
            }
        return self._call("sendMessage", **payload)

    def set_commands(self):
        self._call("setMyCommands", commands=COMMANDS)

    def answer_button(self, callback_id):
        self._call("answerCallbackQuery", callback_query_id=callback_id)

    def clear_buttons(self, chat_id, message_id):
        self._call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id)


def wants_week(text):
    """True when a message is asking for the 7-day overview, not describing something to add."""
    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    return command in ("/week", "/digest") or bool(WEEK_REQUEST.match(text))


def wants_check(text):
    """True when a message asks for a Canvas check right now."""
    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    return command in ("/check", "/refresh") or bool(CHECK_REQUEST.match(text))


# --- reading event times ---


def _moment(side, tz):
    """A Google start/end field as an aware datetime, plus whether it is all-day."""
    if side.get("dateTime"):
        return datetime.fromisoformat(side["dateTime"]).astimezone(tz), False
    return datetime.fromisoformat(side["date"]).replace(tzinfo=tz), True


def ends_after(event, moment, tz):
    return _moment(event["end"], tz)[0] > moment


def starts_at(event, tz):
    return _moment(event["start"], tz)[0]


def when_text(event, tz):
    start, all_day = _moment(event["start"], tz)
    end, _ = _moment(event["end"], tz)
    if all_day:
        return start.strftime("%a %d %b") + " (all day)"
    if private(event).get("kind") in DEADLINE_KINDS:
        return "Due " + end.strftime("%a %d %b, %H:%M")
    return f"{start.strftime('%a %d %b, %H:%M')}–{end.strftime('%H:%M')}"


# --- wording ---


def _clip(text):
    text = text or ""
    return escape(text if len(text) <= NOTE_LIMIT else text[:NOTE_LIMIT].rstrip() + "…")


def _unsure(event):
    summary = event.get("summary", "")
    if summary.startswith("[date unknown]"):
        return "\n⚠️ I couldn't find a date for this. It is in your calendar on the day it was posted."
    if summary.startswith("[?]"):
        return "\n⚠️ I'm not sure about the date. Please check it."
    return ""


def _link(item):
    return f'\n<a href="{escape(item.url)}">Open in Canvas</a>' if item.url else ""


def added_text(event, decision, item, tz):
    lines = [f"📅 <b>Added:</b> {escape(title_of(event))}", when_text(event, tz)]
    if event.get("location"):
        lines.append(_clip(event["location"]))
    if decision.notes:
        lines.append(_clip(decision.notes))
    return "\n".join(lines) + _unsure(event) + _link(item)


def changed_text(event, old, item, tz):
    new_when, old_when = when_text(event, tz), when_text(old, tz)
    lines = [f"✏️ <b>Changed:</b> {escape(title_of(event))}", new_when]
    if new_when != old_when:
        lines.append(f"(was: {old_when})")
    if event.get("location") and event.get("location") != old.get("location"):
        lines.append(_clip(event["location"]))
    return "\n".join(lines) + _unsure(event) + _link(item)


def cancelled_text(old, item, tz):
    return (
        f"❌ <b>Cancelled:</b> {escape(title_of(old))}\n(was: {when_text(old, tz)})\n"
        "Removed from your calendar." + _link(item)
    )


def noted_text(event, decision, item, tz):
    return (
        f"📝 <b>New details:</b> {escape(title_of(event))}\n{when_text(event, tz)}\n"
        f"{_clip(decision.notes or decision.reason)}" + _link(item)
    )


def notice_text(decision, item):
    course = f"<b>{escape(item.course)}:</b> " if item.course else ""
    return f"ℹ️ {course}{_clip(decision.notes or decision.reason)}" + _link(item)


def check_text(result):
    """The answer to /check. Changes worth knowing were already sent as their own messages."""
    new, waiting, outcomes = result["new"], result["waiting"], result["outcomes"]
    if not new:
        return "✅ Checked Canvas: nothing new."
    posts = "1 new post" if new == 1 else f"{new} new posts"
    names = (
        ("created", "added"), ("updated", "changed"), ("cancelled", "cancelled"),
        ("noted", "new details"), ("notified", "notice"), ("ignored", "nothing to do"),
        ("unreadable", "could not read"),
    )
    parts = [f"{outcomes[key]} {label}" for key, label in names if outcomes.get(key)]
    text = f"✅ Checked Canvas: {posts}" + (f" ({', '.join(parts)})." if parts else ".")
    if waiting:
        text += f"\n⏳ {waiting} still to go through. I'll finish on the next check, or send /check again."
    return text


def digest_text(events, now, tz, days=7):
    """Today plus the next `days` full days, grouped by day. None if there is nothing."""
    horizon = (now + timedelta(days=days + 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    coming = sorted(
        (e for e in events if ends_after(e, now, tz) and starts_at(e, tz) < horizon),
        key=lambda e: starts_at(e, tz),
    )
    if not coming:
        return None
    lines, current_day = [f"☀️ <b>Your next {days} days</b>"], None
    for event in coming:
        start, all_day = _moment(event["start"], tz)
        end, _ = _moment(event["end"], tz)
        if start.date() != current_day:
            current_day = start.date()
            gap = (current_day - now.date()).days
            name = "Today" if gap <= 0 else "Tomorrow" if gap == 1 else start.strftime("%A %d %b")
            lines.append(f"\n<b>{name}</b>")
        if all_day:
            time_part = "all day"
        elif private(event).get("kind") in DEADLINE_KINDS:
            time_part = "due " + end.strftime("%H:%M")
        else:
            time_part = start.strftime("%H:%M")
        lines.append(f"• {time_part}  {escape(title_of(event))}")
    return "\n".join(lines)
