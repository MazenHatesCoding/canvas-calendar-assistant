"""Telegram calls this address the moment the student messages the bot or presses a button.

Telegram proves who it is with a secret worked out from the bot token, which the setup
page gave it. Anyone else gets "401 Unauthorized".
"""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import config  # noqa: E402
from assistant.core import Assistant  # noqa: E402
from assistant.web import read_json, reply, same_secret  # noqa: E402


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
        try:
            Assistant().handle_update(update)
        except Exception as error:  # the details may hold course content: log the kind only
            print(f"telegram update failed: {type(error).__name__}")
            # Still "200": Telegram would otherwise keep resending the same message.
        reply(self, 200, "ok")
