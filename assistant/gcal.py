"""Google Calendar: the assistant's own calendar, its events, and a small saved state.

Access is limited to calendars this app created (the "calendar.app.created" permission),
so the assistant cannot see or change any of the user's other calendars.
"""

import json
from datetime import datetime, timedelta
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

from .config import TOKEN_FILE, setting

API = "https://www.googleapis.com/calendar/v3"
SCOPES = ["https://www.googleapis.com/auth/calendar.app.created"]
STATE_FLAG = "assistantState"  # marks the one event that holds the saved state
REMINDER_MINUTES = (24 * 60, 2 * 60)  # one day before and two hours before
DESCRIPTION_LIMIT = 8000  # Google's own limit is a little above this


def load_credentials():
    """From the GOOGLE_TOKEN_JSON setting (used on GitHub) or the local token.json file."""
    raw = setting("GOOGLE_TOKEN_JSON")
    if not raw and TOKEN_FILE.exists():
        raw = TOKEN_FILE.read_text(encoding="utf-8")
    if not raw:
        raise SystemExit("Google is not connected yet. Run: python -m assistant.connect_google")
    return Credentials.from_authorized_user_info(json.loads(raw), SCOPES)


def _moment(text):
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def _source(item):
    return f"Source: {item.url}" if item.url else "Added by you through Telegram."


def private(event):
    """The assistant's own hidden fields on an event."""
    return event.get("extendedProperties", {}).get("private", {})


def title_of(event):
    return private(event).get("title") or event.get("summary", "")


def describe(event):
    """The short form of an event that is shown to the AI."""
    start, end = event.get("start", {}), event.get("end", {})
    return {
        "title": title_of(event),
        "start": (start.get("dateTime") or start.get("date") or "")[:16],
        "end": (end.get("dateTime") or end.get("date") or "")[:16],
    }


def when_fields(start_text, end_text, all_day, fallback_day, tz_name):
    """Google's start and end fields, plus whether a real date was known.

    Falls back to an all-day entry on `fallback_day` when there is no usable start.
    """
    start = _moment(start_text)
    if start is not None and not all_day and len(start_text) > 10:
        end = _moment(end_text)
        if end is None or end <= start:
            end = start + timedelta(hours=1)
        stamp = "%Y-%m-%dT%H:%M:%S"
        return (
            {"dateTime": start.strftime(stamp), "timeZone": tz_name},
            {"dateTime": end.strftime(stamp), "timeZone": tz_name},
            True,
        )
    day = start.date() if start is not None else fallback_day
    return (
        {"date": day.isoformat()},
        {"date": (day + timedelta(days=1)).isoformat()},
        start is not None,
    )


def summary_for(title, low_confidence, dated=True):
    if not dated:
        return "[date unknown] " + title
    return ("[?] " if low_confidence else "") + title


class Calendar:
    def __init__(self, calendar_id, tz_name, credentials=None):
        self.session = AuthorizedSession(credentials or load_credentials())
        self.path = f"/calendars/{quote(calendar_id, safe='')}"
        self.tz_name = tz_name
        self._state_event_id = None

    def _call(self, method, path, **kwargs):
        response = self.session.request(method, API + path, timeout=30, **kwargs)
        if not response.ok:
            raise RuntimeError(f"Google Calendar {method} failed: {response.status_code} {response.text[:300]}")
        return response.json() if response.content else None

    # --- saved state ---
    # Kept in the description of one hidden-away event on 1 January 2000, so no database is
    # needed. (A calendar's own description only holds 1024 characters, which is too little.)

    def load_state(self):
        found = self._call(
            "GET",
            f"{self.path}/events",
            params={"privateExtendedProperty": f"{STATE_FLAG}=1", "maxResults": 1},
        ).get("items", [])
        if not found:
            return {}
        self._state_event_id = found[0]["id"]
        try:
            state = json.loads(found[0].get("description") or "")
        except json.JSONDecodeError:
            return {}
        return state if isinstance(state, dict) else {}

    def save_state(self, state):
        text = json.dumps(state, separators=(",", ":"))
        if self._state_event_id:
            self._call("PATCH", f"{self.path}/events/{self._state_event_id}", json={"description": text})
            return
        created = self._call(
            "POST",
            f"{self.path}/events",
            json={
                "summary": "Assistant data (please keep)",
                "description": text,
                "start": {"date": "2000-01-01"},
                "end": {"date": "2000-01-02"},
                "transparency": "transparent",
                "reminders": {"useDefault": False},
                "extendedProperties": {"private": {STATE_FLAG: "1"}},
            },
        )
        self._state_event_id = created["id"]

    # --- events ---

    def events_since(self, moment):
        """Every event that ends after `moment`."""
        events, page = [], None
        while True:
            params = {"timeMin": moment.isoformat(), "singleEvents": "true", "maxResults": 250}
            if page:
                params["pageToken"] = page
            data = self._call("GET", f"{self.path}/events", params=params)
            events.extend(data.get("items", []))
            page = data.get("nextPageToken")
            if not page:
                return events

    def create(self, decision, item, posted_day):
        start, end, dated = when_fields(
            decision.start, decision.end, decision.all_day, posted_day, self.tz_name
        )
        low = decision.confidence == "low"
        title = decision.title or item.title or "Untitled"
        body = {
            "summary": summary_for(title, low, dated),
            "start": start,
            "end": end,
            "description": f"{decision.notes or ''}\n\n{_source(item)}".strip(),
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": m} for m in REMINDER_MINUTES],
            },
            "extendedProperties": {
                "private": {
                    "source": item.id,
                    "title": title,
                    "kind": decision.kind or "other",
                    "low": "1" if low else "",
                }
            },
        }
        if decision.location:
            body["location"] = decision.location
        return self._call("POST", f"{self.path}/events", json=body)

    def update(self, event, decision, item, posted_day):
        """Apply a newer item to an existing event: new time or place, extra notes."""
        body = {}
        if decision.action == "update":
            if _moment(decision.start):
                body["start"], body["end"], _ = when_fields(
                    decision.start, decision.end, decision.all_day, posted_day, self.tz_name
                )
                # Google keeps the old date/dateTime field unless it is cleared explicitly.
                for side in ("start", "end"):
                    for field in ("date", "dateTime", "timeZone"):
                        body[side].setdefault(field, None)
                # The new date replaces the old one, so its confidence does too.
                low = decision.confidence == "low"
                body["summary"] = summary_for(title_of(event), low)
                body["extendedProperties"] = {"private": {**private(event), "low": "1" if low else ""}}
            if decision.location:
                body["location"] = decision.location

        note = f"[{posted_day.strftime('%d %b')}] {decision.notes or decision.reason}\n{_source(item)}"
        body["description"] = f"{event.get('description', '')}\n\n{note}".strip()[-DESCRIPTION_LIMIT:]
        return self._call("PATCH", f"{self.path}/events/{event['id']}", json=body)

    def delete(self, event):
        self._call("DELETE", f"{self.path}/events/{event['id']}")


def create_calendar(credentials, name, tz_name):
    session = AuthorizedSession(credentials)
    response = session.post(f"{API}/calendars", json={"summary": name, "timeZone": tz_name}, timeout=30)
    if not response.ok:
        raise SystemExit(f"Could not create the calendar: {response.status_code} {response.text[:300]}")
    return response.json()["id"]
