"""Telegram calls this address the moment the student messages the bot or presses a button.

Telegram proves who it is with the TELEGRAM_WEBHOOK_SECRET setting, which
`python -m assistant.connect_telegram` gave it. Anyone else gets "401 Unauthorized".
"""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.core import Assistant, missing_settings  # noqa: E402
from assistant.web import has_secret, reply  # noqa: E402

MAX_BODY = 1_000_000


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        given = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not has_secret(given, "TELEGRAM_WEBHOOK_SECRET"):
            return reply(self, 401, "Unauthorized")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            update = json.loads(self.rfile.read(min(length, MAX_BODY)))
        except ValueError:
            return reply(self, 400, "Bad request")
        if not isinstance(update, dict) or missing_settings():
            return reply(self, 200, "ignored")
        try:
            Assistant().handle_update(update)
        except Exception as error:  # the details may hold course content: log the kind only
            print(f"telegram update failed: {type(error).__name__}")
            # Still "200": Telegram would otherwise keep resending the same message.
        reply(self, 200, "ok")
