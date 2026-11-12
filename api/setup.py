"""The last setup step, used by the page at /setup.html.

Tests every setting against the real service, and tells Telegram to send the bot's
messages to this copy. Needs the CRON_SECRET setting, so only the owner can run it.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import config, telegram as tg  # noqa: E402
from assistant.canvas import CanvasClient, CanvasProblem  # noqa: E402
from assistant.config import setting  # noqa: E402
from assistant.core import DEFAULT_MODELS  # noqa: E402
from assistant.web import bearer, reply_json, same_secret  # noqa: E402


def check_canvas():
    if not setting("CANVAS_BASE_URL") or not setting("CANVAS_TOKEN"):
        return False, "CANVAS_BASE_URL or CANVAS_TOKEN is empty."
    if not setting("CANVAS_BASE_URL").startswith("https://"):
        return False, "CANVAS_BASE_URL must start with https://"
    try:
        client = CanvasClient(setting("CANVAS_BASE_URL"), setting("CANVAS_TOKEN"))
        courses = client.active_courses()
    except CanvasProblem as problem:
        return False, str(problem)
    except Exception:
        return False, "Canvas did not answer as expected. Check CANVAS_BASE_URL."
    return True, f"Signed in. {len(courses)} active course(s) found."


def check_timetable():
    text = setting("TIMETABLE")
    if not text:
        return False, "TIMETABLE is empty."
    return True, f"{len(text.split())} words."


def check_ai():
    if not setting("GEMINI_API_KEY"):
        return False, "GEMINI_API_KEY is empty."
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=setting("GEMINI_API_KEY"))
    options = types.HttpOptions(timeout=15000, retry_options=types.HttpRetryOptions(attempts=1))
    try:
        next(iter(client.models.list(config={"page_size": 1, "http_options": options})), None)
    except errors.APIError as error:
        if error.code in (400, 401, 403):
            return False, "Google rejected the key. Copy it again from aistudio.google.com."
        return False, f"Gemini answered with error {error.code}. Try again in a minute."
    except Exception:
        return False, "Gemini did not answer. Try again in a minute."
    return True, "Key accepted."


def check_google():
    if "GOOGLE_TOKEN_JSON" in config.missing():
        return False, "GOOGLE_TOKEN_JSON is empty or was not pasted completely."
    from assistant.gcal import Calendar

    try:
        Calendar(config.calendar_id(), config.timezone_name()).load_state()
    except Exception:
        return False, "Google refused the saved sign-in. Sign in again and replace GOOGLE_TOKEN_JSON."
    return True, "Calendar reachable."


def check_optional():
    found = config.problems()
    return (False, " ".join(found)) if found else (True, "Nothing to fix.")


def check_telegram(address):
    if not setting("TELEGRAM_BOT_TOKEN"):
        return False, "TELEGRAM_BOT_TOKEN is empty."
    bot = tg.Telegram(setting("TELEGRAM_BOT_TOKEN"))
    try:
        name = bot.me().get("username", "")
        bot.set_webhook(f"https://{address}/api/telegram", config.webhook_secret())
        bot.set_commands()
    except RuntimeError as error:
        return False, str(error)
    return True, f"Connected. Open t.me/{name} and press Start."


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not setting("CRON_SECRET"):
            return reply_json(self, 500, {"error": "CRON_SECRET is not set in this project."})
        if not same_secret(bearer(self), setting("CRON_SECRET")):
            return reply_json(self, 401, {"error": "That is not this project's CRON_SECRET."})
        # The project's lasting address, not the address of this one deployment: Telegram
        # must keep reaching the newest version after every redeploy.
        address = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "").strip() or (
            self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        ).split(",")[0].strip()
        checks = [
            ("Canvas", check_canvas),
            ("Timetable", check_timetable),
            ("Gemini AI", check_ai),
            ("Google Calendar", check_google),
            ("Telegram bot", lambda: check_telegram(address)),
        ]
        if config.problems():
            checks.append(("Optional settings", check_optional))
        results = []
        for name, check in checks:
            try:
                ok, detail = check()
            except Exception as error:
                ok, detail = False, f"Unexpected problem ({type(error).__name__})."
            results.append({"name": name, "ok": ok, "detail": detail})
        reply_json(self, 200, {"checks": results, "ready": all(r["ok"] for r in results)})
