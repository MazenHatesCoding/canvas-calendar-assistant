"""The hourly check, started by the schedule in vercel.json.

Vercel calls this address with the CRON_SECRET setting in the Authorization header.
Anyone else gets "401 Unauthorized".
"""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import config  # noqa: E402
from assistant.core import Assistant  # noqa: E402
from assistant.web import bearer, reply, same_secret  # noqa: E402


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
            return reply(self, 500, "failed")
        reply(self, 500 if outcome == "failed" else 200, outcome)

    do_POST = do_GET
