"""Stand-ins for Google Calendar, Telegram, Canvas and the AI, so whole checks and bot
conversations can run with no network. Used by test_flows.py."""

import copy
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from assistant import core, extractor as ex, gcal
from assistant.canvas import Item

TZ = ZoneInfo("Africa/Cairo")
OWNER = 4242  # the student's Telegram chat

SETTINGS = {
    "CANVAS_BASE_URL": "https://canvas.example.edu",
    "CANVAS_TOKEN": "canvas-token",
    "GEMINI_API_KEY": "gemini-key",
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "GOOGLE_CALENDAR_ID": "cal@example",
    "GOOGLE_TOKEN_JSON": '{"refresh_token": "r", "client_id": "c", "client_secret": "s"}',
    "TIMETABLE": "I am in group 6.",
    "CANVAS_TOKEN_CREATED": "",
    "TELEGRAM_CHAT_ID": "",
    "TIMEZONE": "",
    "LOOKBACK_DAYS": "",
    "DIGEST_HOUR": "off",
    "GEMINI_MODELS": "",
}


class Response:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data
        self.ok = 200 <= status < 300
        self.content = b"" if data is None else b"x"
        self.text = "" if data is None else str(data)

    def json(self):
        return self._data


def _end_moment(event):
    end = event["end"]
    if end.get("dateTime"):
        moment = datetime.fromisoformat(end["dateTime"])
        return moment if moment.tzinfo else moment.replace(tzinfo=TZ)
    return datetime.fromisoformat(end["date"]).replace(tzinfo=TZ)


class GoogleCalendar:
    """One calendar's events behind the same web calls the real service answers.
    Shared by every "request" in a test, like the real calendar is."""

    def __init__(self):
        self.events = {}  # id -> event
        self.counter = 0
        self.fail = None  # (method, status): answer that method with an error
        self.before_write = None  # called once before the next state write, to stage a race
        self.calls = []

    # What the code under test sees as its web session.
    def request(self, method, url, timeout=None, params=None, json=None, headers=None):
        self.calls.append((method, url))
        if self.fail and self.fail[0] == method:
            if isinstance(self.fail[1], Exception):
                raise self.fail[1]
            return Response(self.fail[1], {"error": "refused"})
        tail = url.split("/events", 1)[1].lstrip("/")
        if method == "GET":
            return Response(200, {"items": copy.deepcopy(self._list(params or {}))})
        if method == "POST":
            return Response(200, copy.deepcopy(self._store(self._new_id(), json)))
        if tail not in self.events:
            return Response(404, {"error": "not found"})
        if method == "DELETE":
            del self.events[tail]
            return Response(204)
        if method == "PATCH":
            if "description" in json and self.before_write:
                hook, self.before_write = self.before_write, None
                hook()
            event = self.events[tail]
            wanted = (headers or {}).get("If-Match")
            if wanted and wanted != event["etag"]:
                return Response(412, {"error": "changed"})
            self._merge(event, json)
            return Response(200, copy.deepcopy(self._store(tail, event)))
        raise AssertionError(f"unexpected call {method} {url}")

    def _new_id(self):
        self.counter += 1
        return f"ev{self.counter}"

    def _store(self, event_id, body):
        self.counter += 1
        event = {**copy.deepcopy(body), "id": event_id, "etag": f'"{self.counter}"'}
        self.events[event_id] = event
        return event

    @staticmethod
    def _merge(event, changes):
        """Google's PATCH: objects are merged, and a field set to null is removed."""
        for key, value in changes.items():
            if isinstance(value, dict) and isinstance(event.get(key), dict):
                GoogleCalendar._merge(event[key], value)
            elif value is None:
                event.pop(key, None)
            else:
                event[key] = copy.deepcopy(value)
        for key in [k for k, v in event.items() if isinstance(v, dict)]:
            event[key] = {k: v for k, v in event[key].items() if v is not None}

    def _list(self, params):
        found = list(self.events.values())
        if "privateExtendedProperty" in params:
            name, _, value = params["privateExtendedProperty"].partition("=")
            found = [e for e in found if gcal.private(e).get(name) == value]
        if "timeMin" in params:
            floor = datetime.fromisoformat(params["timeMin"])
            found = [e for e in found if _end_moment(e) > floor]
        return found

    # For the tests themselves.
    def state(self):
        import json as json_module

        for event in self.events.values():
            if gcal.private(event).get(gcal.STATE_FLAG):
                return json_module.loads(event["description"])
        return {}

    def set_state(self, state):
        import json as json_module

        calendar = gcal.Calendar.__new__(gcal.Calendar)
        calendar.session, calendar.path, calendar.tz_name = self, "/calendars/x", "Africa/Cairo"
        calendar._state_event_id, calendar._loaded_state = None, {}
        _, etag = calendar._read_state()
        calendar._write_state(json_module.loads(json_module.dumps(state)), etag)

    def real_events(self):
        return [e for e in self.events.values() if not gcal.private(e).get(gcal.STATE_FLAG)]

    def titles(self):
        return sorted(e["summary"] for e in self.real_events())


class Telegram:
    """Records what the bot sends. `broken` makes every send fail."""

    sent = []  # (chat, text, buttons): shared, reset by `world`
    broken = False

    def __init__(self, token):
        pass

    def send(self, chat_id, text, buttons=None):
        if Telegram.broken:
            raise RuntimeError("Telegram sendMessage failed: down")
        if len(text) > 4096:
            raise RuntimeError("Telegram sendMessage failed: message is too long")
        Telegram.sent.append((chat_id, text, buttons))

    def answer_button(self, callback_id):
        pass

    def clear_buttons(self, chat_id, message_id):
        pass


class Canvas:
    items = []
    problem = None

    def __init__(self, base_url, token):
        pass

    def recent_items(self, lookback_days):
        if Canvas.problem:
            raise Canvas.problem
        return sorted(Canvas.items, key=lambda item: item.posted_at)


class AI:
    """answers: item id -> Decision, an exception to raise, or a function of (item, known)."""

    answers = {}
    asked = []

    def __init__(self, *args, **kwargs):
        self.out_of_quota, self.stalled, self.answered = [], [], set()

    def decide(self, item, known, manual=False):
        AI.asked.append(item.id)
        answer = AI.answers.get(item.id, ex.Decision(reason="nothing to do", action="ignore"))
        if callable(answer):
            answer = answer(item, known)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def post(number, title="Quiz", hours_ago=1, kind="announcement", course="Introduction to Databases"):
    return Item(
        id=f"{kind}-{number}",
        kind=kind,
        course=course,
        title=title,
        body=title,
        posted_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        url=f"https://canvas.example.edu/x/{number}",
    )


def decision(action="create", days=3, **fields):
    """A decision about something `days` from now at 14:00."""
    when = (datetime.now(TZ) + timedelta(days=days)).strftime("%Y-%m-%dT14:00")
    values = {"reason": "because", "action": action}
    if action in ("create", "update") and not fields.get("cancelled") and "start" not in fields:
        values.update(start=when, title="Databases: Quiz", kind="quiz")
    values.update(fields)
    return ex.Decision(**values)


def text_update(update_id, text, chat=OWNER, chat_type="private"):
    return {
        "update_id": update_id,
        "message": {"text": text, "date": int(datetime.now().timestamp()), "chat": {"id": chat, "type": chat_type}},
    }


def button_update(update_id, data, chat=OWNER):
    return {
        "update_id": update_id,
        "callback_query": {"id": "q", "data": data, "message": {"message_id": 9, "chat": {"id": chat}}},
    }


@contextmanager
def world(state=None, **settings):
    """Everything outside the assistant replaced by the stand-ins above. Yields the calendar."""
    google = GoogleCalendar()
    if state is not None:
        google.set_state(state)
    Telegram.sent, Telegram.broken = [], False
    Canvas.items, Canvas.problem = [], None
    AI.answers, AI.asked = {}, []
    with mock.patch.dict(os.environ, {**SETTINGS, **settings}), \
            mock.patch.object(gcal, "AuthorizedSession", lambda credentials: google), \
            mock.patch.object(gcal, "load_credentials", lambda: None), \
            mock.patch.object(core.tg, "Telegram", Telegram), \
            mock.patch.object(core, "CanvasClient", Canvas), \
            mock.patch.object(core, "Extractor", AI):
        yield google


def said():
    """The texts sent to the student so far."""
    return [text for _, text, _ in Telegram.sent]
