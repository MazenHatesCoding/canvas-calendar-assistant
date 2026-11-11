"""Google sign-in for people setting up their own copy, used by the page at /connect.html.

Only the project's main site offers this: it needs the GOOGLE_OAUTH_CLIENT_ID and
GOOGLE_OAUTH_CLIENT_SECRET settings, which a personal copy does not have.

GET  sends the visitor to Google's sign-in page.
POST takes the code Google handed back, creates the assistant's calendar in that Google
     account, and returns the value to save as GOOGLE_TOKEN_JSON. Nothing is stored or
     logged here; the value goes straight back to the visitor's browser.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import available_timezones

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.config import setting  # noqa: E402
from assistant.web import read_json, reply, reply_json  # noqa: E402

SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
# A "Desktop app" sign-in ends on an address on the visitor's own computer. Nothing listens
# there, so the browser shows an error page, and the visitor copies the address from it.
RETURN_ADDRESS = "http://localhost:8765/"
CALENDAR_NAME = "University"
TOKEN_URL = "https://oauth2.googleapis.com/token"
NOT_OFFERED = "This copy does not offer Google sign-in. Use the connect page of the project's main site."


def client():
    return setting("GOOGLE_OAUTH_CLIENT_ID"), setting("GOOGLE_OAUTH_CLIENT_SECRET")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        client_id, secret = client()
        if not client_id or not secret:
            return reply(self, 404, NOT_OFFERED)
        state = parse_qs(urlparse(self.path).query).get("state", [""])[0][:100]
        query = urlencode({
            "client_id": client_id,
            "redirect_uri": RETURN_ADDRESS,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        })
        self.send_response(302)
        self.send_header("Location", f"https://accounts.google.com/o/oauth2/v2/auth?{query}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_POST(self):
        client_id, secret = client()
        if not client_id or not secret:
            return reply_json(self, 404, {"error": NOT_OFFERED})
        body = read_json(self) or {}
        code = str(body.get("code") or "")
        timezone = str(body.get("timezone") or "")
        if timezone not in available_timezones():
            timezone = "Africa/Cairo"
        if not code or len(code) > 2000:
            return reply_json(self, 400, {"error": "The sign-in code is missing."})
        try:
            answer = requests.post(TOKEN_URL, timeout=20, data={
                "code": code,
                "client_id": client_id,
                "client_secret": secret,
                "redirect_uri": RETURN_ADDRESS,
                "grant_type": "authorization_code",
            })
            token = answer.json()
        except (requests.RequestException, ValueError):
            return reply_json(self, 502, {"error": "Google did not answer. Please try again."})
        if not answer.ok or not token.get("refresh_token"):
            return reply_json(self, 400, {
                "error": "Google did not accept that code. Codes work once and expire after a few "
                         "minutes: press the sign-in button and try again."
            })
        if SCOPE not in token.get("scope", "").split():
            return reply_json(self, 400, {
                "error": "The calendar permission was not granted. Sign in again and leave the box ticked."
            })
        try:
            made = requests.post(
                "https://www.googleapis.com/calendar/v3/calendars",
                headers={"Authorization": f"Bearer {token['access_token']}"},
                json={"summary": CALENDAR_NAME, "timeZone": timezone},
                timeout=20,
            )
            calendar_id = made.json()["id"]
        except (requests.RequestException, ValueError, KeyError):
            return reply_json(self, 502, {"error": "Signed in, but the calendar could not be created. Please try again."})
        value = json.dumps({
            "refresh_token": token["refresh_token"],
            "client_id": client_id,
            "client_secret": secret,
            "token_uri": TOKEN_URL,
            "calendar_id": calendar_id,
        }, separators=(",", ":"))
        reply_json(self, 200, {"GOOGLE_TOKEN_JSON": value, "calendar": CALENDAR_NAME})
