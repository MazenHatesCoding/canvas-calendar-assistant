"""Settings. They come from environment variables: a local .env file, or the host's settings."""

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
