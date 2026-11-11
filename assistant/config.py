"""Settings. They come from environment variables: a local .env file, or the host's settings."""

import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
TOKEN_FILE = ROOT / "token.json"
CREDENTIALS_FILE = ROOT / "credentials.json"
TIMETABLE_FILE = ROOT / "timetable.txt"


def load():
    load_dotenv(ENV_FILE)


def setting(name, default=""):
    # An empty value counts as "not set".
    return os.getenv(name, "").strip() or default


def timetable():
    """The student's own description of their group and weekly sessions, in plain sentences."""
    text = setting("TIMETABLE")
    if not text and TIMETABLE_FILE.exists():
        text = TIMETABLE_FILE.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise SystemExit("Your timetable is missing. Write it in timetable.txt or the TIMETABLE setting.")
    return text


def google_connection():
    """The saved Google sign-in, from the GOOGLE_TOKEN_JSON setting or the local token.json.
    Empty if Google is not connected yet."""
    raw = setting("GOOGLE_TOKEN_JSON")
    if not raw and TOKEN_FILE.exists():
        raw = TOKEN_FILE.read_text(encoding="utf-8")
    try:
        saved = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return saved if isinstance(saved, dict) else {}


def calendar_id():
    """The assistant's own calendar. The sign-in page stores it inside GOOGLE_TOKEN_JSON."""
    return setting("GOOGLE_CALENDAR_ID") or google_connection().get("calendar_id", "")


def webhook_secret():
    """What Telegram must present when it calls the bot's address. It is worked out from
    the bot token, so there is no extra setting to fill in. Empty without a bot token."""
    token = setting("TELEGRAM_BOT_TOKEN")
    return hashlib.sha256(f"telegram-webhook:{token}".encode()).hexdigest() if token else ""


def missing():
    """Names of the settings the assistant cannot work without and does not have."""
    names = [n for n in ("CANVAS_BASE_URL", "CANVAS_TOKEN", "GEMINI_API_KEY") if not setting(n)]
    if not google_connection().get("refresh_token") or not calendar_id():
        names.append("GOOGLE_TOKEN_JSON")
    if not setting("TIMETABLE") and not TIMETABLE_FILE.exists():
        names.append("TIMETABLE")
    return names

