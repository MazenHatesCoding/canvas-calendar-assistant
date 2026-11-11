"""Turn one item into a decision: create an event, update one, add a note, notify, or ignore."""

import time
from datetime import timedelta
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError


class AIUnavailable(Exception):
    """The AI service cannot answer right now. Worth retrying on a later run."""

    def __init__(self, message, quota=False):
        super().__init__(message)
        self.quota = quota  # True when the free daily quota is simply used up


class Unreadable(Exception):
    """The AI answered, but not with a usable decision. Retrying the same item will not help."""


class Decision(BaseModel):
    reason: str  # one short sentence explaining the decision
    action: Literal["create", "update", "note", "notify", "ignore"]
    target_key: Optional[str] = None  # key of the existing event, for update/note
    kind: Optional[
        Literal["quiz", "exam", "assignment", "project", "lab_task", "schedule_change", "other"]
    ] = None
    title: Optional[str] = None
    start: Optional[str] = None  # local time, YYYY-MM-DDTHH:MM
    end: Optional[str] = None
    all_day: bool = False
    cancelled: bool = False  # update only: the event no longer happens and is removed
    location: Optional[str] = None
    notes: Optional[str] = None  # what to prepare, topics covered, links
    confidence: Literal["high", "low"] = "high"


RULES = """You are the personal assistant of one university student. You read one Canvas \
announcement or message at a time and decide what belongs in the student's calendar.

The student only wants things they must DO or ATTEND in the future:
- quizzes, exams, assignments, project deliverables, lab tasks, submission deadlines
- changes to those (extensions, postponements, cancellations)
- schedule changes that affect the student's own sessions (moved, cancelled or online classes)

Choose one action:
- "create": a new thing to do or attend that is not in the existing events list.
- "update": the item changes an existing event (new date, new time, cancellation). Set \
target_key to that event's key and give the full new start/end. If the event is cancelled, set \
cancelled=true instead: it will be removed from the calendar.
- "note": the item only adds useful details to an existing event (exam scope, question format, \
what to bring). Set target_key and put the details in notes. If no existing event matches, use "ignore".
- "notify": the student should be told, but nothing belongs in the calendar. Use this when one \
of the student's regular classes is cancelled or postponed with no new date. Put a one-line \
message for the student in notes.
- "ignore": everything else. Always ignore: grades or marks being posted, slides or materials \
uploaded, general remarks, good-luck messages, and anything addressed only to groups other than \
the student's group.

Conflicting information:
- When this item disagrees with an existing event about the same thing (date, time, place, \
whether it happens), this item is newer, so it is the truth: "update" the existing event to match it.
- Before treating two things as a conflict, check they are really about the same thing for the \
same audience. An item aimed at another group or section does not conflict with the student's \
event and must never change it: "ignore" it. Use the wording of the item to judge this. If it is \
unclear who the item is for, apply it and set confidence="low".

Rules for dates and times:
- All times are local time in {timezone}. Write start and end as YYYY-MM-DDTHH:MM (24-hour).
- Resolve relative dates ("tomorrow", "next week", "this Thursday") from the date the item was \
POSTED, never from today. Use the day list provided to get weekdays right.
- Unless the student's own notes say otherwise, the university week runs Sunday to Thursday. \
"Next week" means the first such week that starts after the posting date.
- When something happens "during your tutorial/lab/lecture" or "in your scheduled session", use \
the student's own session for that course from their timetable, on the right week. A phrase like \
"starting Sunday" names the week; the student still attends on their own session day.
- Dates written like 6/11 are day/month.
- A deadline: put the deadline moment in "end" and set "start" to 30 minutes before it. A \
deadline time of "11:59" with no AM/PM means 23:59.
- If only a day is known and no time can be worked out, set all_day=true and use YYYY-MM-DD for start.
- If the date is vague or you had to guess, set confidence="low". Never invent a date: if there \
is no usable date at all for something the student must do, still "create" it with start=null \
and confidence="low".

Write the title as "<short course name>: <what>", for example "Database Systems: Quiz" or \
"Software Engineering: Deliverable 2". The short course name is the course name without a \
leading "Introduction to". Keep <what> to a few plain words; quiz numbers are not needed. \
Keep notes short and factual. Leave fields you do not need as null.

The student describes their group and weekly timetable like this:

{timetable}"""

MANUAL_PREFACE = (
    "The item below was typed by the student themselves, to add, change or cancel something in "
    "their calendar. It is about them, so never ignore it because of groups. Resolve dates from "
    'the time it was sent. Use "ignore" only if it contains nothing to add, change or cancel.\n\n'
)

RETRY_WAITS = (20, 40, 60)  # seconds, for per-minute limits and a busy model
CALL_TIMEOUT = 60  # seconds one request may take before the next model is tried
MIN_TIMEOUT = 10  # Google rejects a request that allows less time than this


def _day_list(posted_local):
    """Dates with weekday names around the posting date, so the model never has to count days."""
    days = [posted_local.date() + timedelta(days=n) for n in range(-1, 43)]
    return ", ".join(day.strftime("%a %Y-%m-%d") for day in days)


class Extractor:
    def __init__(self, api_key, models, tz_name, timetable,
                 retry_waits=RETRY_WAITS, call_timeout=CALL_TIMEOUT, give_up_after=None, reserve=0):
        """models: best first. The free tier has a small daily quota per model, so when one
        runs out the next one takes over.
        retry_waits: seconds to wait between tries when the service is busy.
        call_timeout: seconds one request may take.
        give_up_after: seconds a whole decision may take, waits included (None: no limit).
            Set when a person is waiting for the answer.
        reserve: seconds of give_up_after kept back for the last model, so that models
            ahead of it which do not answer cannot use up all the time."""
        self.retry_waits = tuple(retry_waits)
        self.call_timeout = call_timeout
        self.give_up_after = give_up_after
        self.reserve = reserve
        # What this run found out about the models, for the caller to remember.
        self.out_of_quota = []  # daily quota used up
        self.stalled = []  # given the full call_timeout and still no answer
        self.answered = set()
        self.client = self._client(api_key)
        self.models = [m.strip() for m in models if m.strip()]
        self.tz = ZoneInfo(tz_name)
        self.system = RULES.format(timezone=tz_name, timetable=timetable)

    @staticmethod
    def _client(api_key):
        return genai.Client(api_key=api_key)

    def decide(self, item, known_events, manual=False):
        """known_events: list of dicts with key, title, start, end.
        manual: the item is a note the student typed themselves, not a Canvas post."""
        posted = item.posted_at.astimezone(self.tz)
        events = "\n".join(
            f"- key={e['key']} | {e['title']} | start={e['start']} | end={e['end']}"
            for e in known_events
        )
        prompt = (
            f"Posted: {posted.strftime('%A %Y-%m-%d %H:%M')}\n"
            f"Days: {_day_list(posted)}\n\n"
            f"Existing events:\n{events or '(none)'}\n\n"
            f"Source: {item.kind}\nCourse: {item.course}\nTitle: {item.title}\n\n{item.body}"
        )
        if manual:
            prompt = MANUAL_PREFACE + prompt
        text = self._ask(prompt)
        try:
            return Decision.model_validate_json(text or "")
        except ValidationError:
            raise Unreadable("the AI did not return a usable decision") from None

    def _ask(self, prompt):
        started = time.monotonic()
        waits = list(self.retry_waits)

        def spare(after=0):
            """Seconds the current model may still use, `after` seconds from now (None: no limit)."""
            if not self.give_up_after:
                return None
            left = self.give_up_after - (time.monotonic() - started) - after
            return left if len(self.models) == 1 else left - self.reserve

        def skip_to_last():
            print(f"   (switching to {self.models[-1]})")
            self.models = self.models[-1:]

        while True:
            model, last = self.models[0], len(self.models) == 1
            room = spare()
            if room is not None and room < MIN_TIMEOUT:
                if last:
                    raise AIUnavailable("AI service too slow")
                skip_to_last()
                waits = list(self.retry_waits)
                continue
            # The last model gets all the time that is left: there is nothing to save it for.
            timeout = self.call_timeout if room is None else room if last else min(self.call_timeout, room)
            config = types.GenerateContentConfig(
                system_instruction=self.system,
                response_mime_type="application/json",
                response_schema=Decision,
                temperature=0,
                # The library's own silent retries are off: the waiting is done here, in the open.
                http_options=types.HttpOptions(
                    timeout=int(timeout * 1000), retry_options=types.HttpRetryOptions(attempts=1)
                ),
            )
            try:
                text = self.client.models.generate_content(
                    model=model, contents=prompt, config=config
                ).text
                self.answered.add(model)
                return text
            except errors.APIError as error:
                code, daily_quota = error.code, error.code == 429 and "PerDay" in str(error)
            except Exception as error:  # no answer in time, or no connection
                print(f"   (no answer from {model}: {type(error).__name__})")
                code, daily_quota = None, False

            stalled = code in (504, None)  # 504: Google gave up waiting for its own model
            self.answered.discard(model)
            if daily_quota and model not in self.out_of_quota:
                self.out_of_quota.append(model)
            # A model cut short by our own lack of time is not blamed for it.
            if stalled and timeout >= self.call_timeout and model not in self.stalled:
                self.stalled.append(model)

            if (daily_quota or stalled or code == 404) and not last:
                # Used up for today, gone, or not answering: move on.
                if stalled and self.give_up_after:
                    # Someone is waiting, and the next model may hang as well. Go straight
                    # to the last one, which has time kept for it.
                    skip_to_last()
                else:
                    print(f"   ({model} unavailable, switching to {self.models[1]})")
                    self.models.pop(0)
                waits = list(self.retry_waits)
            elif (code in (429, 500, 503, 504, None) and waits and not daily_quota
                  and (room is None or spare(after=waits[0]) >= MIN_TIMEOUT)):
                time.sleep(waits.pop(0))
            elif self.give_up_after and not last:
                # Busy and out of patience, but someone is waiting: the last model may still answer.
                skip_to_last()
                waits = list(self.retry_waits)
            else:
                problem = f"AI service error {code}" if code else "AI service not answering"
                raise AIUnavailable(problem, quota=daily_quota) from None
