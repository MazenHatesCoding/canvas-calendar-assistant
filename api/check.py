"""The hourly check, started by the schedule in vercel.json.

Vercel calls this address with the CRON_SECRET setting in the Authorization header.
Anyone else gets "401 Unauthorized".
"""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from assistant import config, telegram as tg  # noqa: E402
from assistant.core import Assistant  # noqa: E402
from assistant.gcal import CalendarProblem  # noqa: E402
from assistant.web import bearer, reply, same_secret  # noqa: E402

ALERT_HOUR = 9  # local time


def report_sign_in():
    """Google refused the saved sign-in, so nothing works, and the saved state (which holds
    the student's chat) cannot be read either. If the chat is given as TELEGRAM_CHAT_ID the
    student is told once a day: with no state to remember it in, "once" is one fixed hour."""
    chat, token = config.setting("TELEGRAM_CHAT_ID"), config.setting("TELEGRAM_BOT_TOKEN")
    if not token or not chat.lstrip("-").isdigit():
        return
    if datetime.now(ZoneInfo(config.timezone_name())).hour != ALERT_HOUR:
        return
    try:
        tg.Telegram(token).send(int(chat), (
            "⚠️ Google no longer accepts my saved sign-in, so I have stopped working. Sign in "
            "again on the connect page, replace GOOGLE_TOKEN_JSON in Vercel, and redeploy."
        ))
    except RuntimeError as error:
        print(error)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not same_secret(bearer(self), config.setting("CRON_SECRET")):
            return reply(self, 401, "Unauthorized")
        missing = config.missing()
        if missing:
            print("Missing settings: " + ", ".join(missing))
            return reply(self, 500, "Missing settings")
        try:
            outcome = Assistant().check_canvas()
        except Exception as error:  # the details may hold course content: log the kind only
            print(f"check failed: {type(error).__name__}")
            if isinstance(error, CalendarProblem) and error.signed_out:
                print("Google refused the saved sign-in: GOOGLE_TOKEN_JSON must be replaced.")
                report_sign_in()
            return reply(self, 500, "failed")
        reply(self, 500 if outcome == "failed" else 200, outcome)

    do_POST = do_GET
