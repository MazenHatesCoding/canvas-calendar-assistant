"""Telegram calls this address the moment the student messages the bot or presses a button.

Telegram proves who it is with a secret worked out from the bot token, which the setup
page gave it. Anyone else gets "401 Unauthorized".
"""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import config, telegram as tg  # noqa: E402
from assistant.core import Assistant  # noqa: E402
from assistant.gcal import CalendarProblem  # noqa: E402
from assistant.web import read_json, reply, same_secret  # noqa: E402

SIGNED_OUT = (
    "⚠️ Google no longer accepts my saved sign-in, so I cannot open your calendar. Sign in "
    "again on the connect page, replace GOOGLE_TOKEN_JSON in Vercel, and redeploy."
)
NO_CALENDAR = "⚠️ I can't reach Google Calendar right now. Please send that again in a few minutes."


def answer_anyway(update, text):
    """Reply without the saved state, which lives in the calendar that cannot be reached.
    Only in a private chat, and only with a message that gives nothing away."""
    message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("type") != "private" or not config.setting("TELEGRAM_BOT_TOKEN"):
        return
    try:
        tg.Telegram(config.setting("TELEGRAM_BOT_TOKEN")).send(chat["id"], text)
    except (RuntimeError, KeyError) as error:
        print(f"telegram: could not reply ({type(error).__name__})")


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        given = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not same_secret(given, config.webhook_secret()):
            return reply(self, 401, "Unauthorized")
        update = read_json(self)
        if update is None:
            return reply(self, 400, "Bad request")
        if config.missing():
            return reply(self, 200, "ignored")
        # Whatever happens the answer is "200": Telegram would otherwise keep resending the
        # same message. The details may hold course content, so only the kind is logged.
        try:
            assistant = Assistant()
        except Exception as error:
            print(f"telegram update failed at the start: {type(error).__name__}")
            signed_out = isinstance(error, CalendarProblem) and error.signed_out
            answer_anyway(update, SIGNED_OUT if signed_out else NO_CALENDAR)
            return reply(self, 200, "ok")
        try:
            assistant.handle_update(update)
        except Exception as error:  # the student has been told by handle_update
            print(f"telegram update failed: {type(error).__name__}")
        reply(self, 200, "ok")
