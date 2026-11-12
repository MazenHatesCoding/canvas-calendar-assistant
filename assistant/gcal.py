"""Google Calendar: the assistant's own calendar, its events, and a small saved state.

Access is limited to calendars this app created (the "calendar.app.created" permission),
so the assistant cannot see or change any of the user's other calendars.
"""

import copy
import json
from datetime import datetime, timedelta
from urllib.parse import quote

import requests
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

from .config import google_connection

API = "https://www.googleapis.com/calendar/v3"
SCOPES = ["https://www.googleapis.com/auth/calendar.app.created"]
STATE_FLAG = "assistantState"  # marks the one event that holds the saved state
REMINDER_MINUTES = (24 * 60, 2 * 60)  # one day before and two hours before
DESCRIPTION_LIMIT = 8000  # Google's own limit is a little above this
# Google cuts a description off at 8,192 characters and still answers "saved" (measured on
# 12 Nov 2026). A saved state cut short cannot be read back, so one that long is never sent.
STATE_LIMIT = 8192
GET_TIMEOUT = 15  # seconds; reading is tried twice
SAVE_ATTEMPTS = 4


class CalendarProblem(RuntimeError):
    """Google Calendar did not do what was asked.

    refused: Google read the request and rejected it, so sending it again will not help.
    signed_out: the saved Google sign-in no longer works; the student has to sign in again.
    """

    def __init__(self, message, refused=False, signed_out=False):
        super().__init__(message)
        self.refused, self.signed_out = refused, signed_out


def _problem(method, response):
    status = response.status_code
    return CalendarProblem(
        f"Google Calendar {method} failed: {status} {response.text[:300]}",
        refused=status in (400, 413),
        signed_out=status == 401,
    )


def load_credentials():
    saved = google_connection()
    if not saved:
        raise SystemExit("Google is not connected yet. See the README for how to sign in.")
    return Credentials.from_authorized_user_info(saved, SCOPES)


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


def _plain(values):
    return all(isinstance(value, (str, int)) for value in values)


def merge_changes(base, mine, theirs):
    """`theirs` with the changes made between `base` and `mine` applied on top.

    Used when two requests loaded the same saved state: each one's changes survive, and
    where both changed the same value, `mine` wins. Dictionaries are merged key by key.
    Lists of plain values (finished posts, handled messages) are merged entry by entry:
    what I added is added, what I removed is removed.
    """
    result = copy.deepcopy(theirs)
    for key in set(base) | set(mine):
        if key not in mine:
            result.pop(key, None)  # I removed it
        elif key not in base or mine[key] != base[key]:
            old, other = base.get(key), result.get(key)
            if isinstance(mine[key], dict) and isinstance(other, dict):
                result[key] = merge_changes(old if isinstance(old, dict) else {}, mine[key], other)
            elif (isinstance(mine[key], list) and isinstance(other, list)
                  and isinstance(old or [], list) and _plain(mine[key] + other + (old or []))):
                old = old or []
                removed = set(old) - set(mine[key])
                kept = [value for value in other if value not in removed]
                result[key] = kept + [v for v in mine[key] if v not in old and v not in kept]
            else:
                result[key] = copy.deepcopy(mine[key])
    return result


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
        self._loaded_state = {}

    def _send(self, method, path, **kwargs):
        """One request. Every way it can go wrong surfaces as a RuntimeError, so callers
        have a single thing to catch: no connection, a timeout, or a refused sign-in."""
        # Reading changes nothing, so a read that got no answer is simply tried once more.
        for attempt in (1, 2) if method == "GET" else (2,):
            try:
                return self.session.request(
                    method, API + path, timeout=GET_TIMEOUT if method == "GET" else 30, **kwargs
                )
            except (requests.RequestException, GoogleAuthError) as error:
                if attempt == 1 and isinstance(error, (requests.ConnectionError, requests.Timeout)):
                    continue
                raise CalendarProblem(
                    f"Google Calendar {method} failed: {type(error).__name__}",
                    signed_out=isinstance(error, RefreshError),
                ) from None

    def _call(self, method, path, **kwargs):
        response = self._send(method, path, **kwargs)
        if not response.ok:
            raise _problem(method, response)
        return response.json() if response.content else None

    # --- saved state ---
    # Kept in the description of one hidden-away event on 1 January 2000, so no database is
    # needed. (A calendar's own description only holds 1024 characters, which is too little.)

    #
    # Two requests can be at work at once (a Telegram message arriving during the hourly
    # check). So saving never overwrites blindly: it re-reads the stored state, applies only
    # what this request changed, and writes only if nobody else wrote in between.

    def _read_state(self):
        """The stored state and the event's version tag, or ({}, None) if there is none yet."""
        found = self._call(
            "GET",
            f"{self.path}/events",
            params={"privateExtendedProperty": f"{STATE_FLAG}=1", "maxResults": 1},
        ).get("items", [])
        if not found:
            return {}, None
        self._state_event_id = found[0]["id"]
        try:
            state = json.loads(found[0].get("description") or "")
        except json.JSONDecodeError:
            print("The saved state could not be read: starting again from nothing.")
            state = {}
        return (state if isinstance(state, dict) else {}), found[0].get("etag")

    def load_state(self):
        state, _ = self._read_state()
        self._loaded_state = copy.deepcopy(state)
        return state

    def save_state(self, state):
        """Store `state`, keeping changes another request saved since it was loaded.
        `state` is updated in place to what was actually stored."""
        for _ in range(SAVE_ATTEMPTS):
            stored, etag = self._read_state()
            merged = merge_changes(self._loaded_state, state, stored)
            if self._write_state(merged, etag):
                break
        else:
            raise RuntimeError("Google Calendar: the saved state kept changing, could not save")
        self._adopt(state, merged)

    def claim(self, state, key, value, is_free):
        """Set state[key] = value and save, but only if `is_free(stored value)` holds at the
        moment of writing. Returns False if another request holds it. This is what stops two
        hourly checks from running at the same time."""
        for _ in range(SAVE_ATTEMPTS):
            stored, etag = self._read_state()
            if not is_free(stored.get(key)):
                return False
            merged = merge_changes(self._loaded_state, state, stored)
            merged[key] = value
            if self._write_state(merged, etag):
                self._adopt(state, merged)
                return True
        return False

    def _adopt(self, state, merged):
        self._loaded_state = copy.deepcopy(merged)
        state.clear()
        state.update(merged)

    def _write_state(self, state, etag):
        """False if someone else changed the stored state first."""
        text = json.dumps(state, separators=(",", ":"))
        if len(text) > STATE_LIMIT:
            raise CalendarProblem("Google Calendar: the saved state is too large to store")
        if self._state_event_id:
            response = self._send(
                "PATCH",
                f"{self.path}/events/{self._state_event_id}",
                json={"description": text},
                headers={"If-Match": etag} if etag else {},
            )
            if response.status_code == 412:
                return False
            if not response.ok:
                raise _problem("PATCH", response)
            return True
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
        return True

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
