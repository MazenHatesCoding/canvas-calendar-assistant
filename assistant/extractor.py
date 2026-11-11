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


def _day_list(posted_local):
    """Dates with weekday names around the posting date, so the model never has to count days."""
    days = [posted_local.date() + timedelta(days=n) for n in range(-1, 43)]
    return ", ".join(day.strftime("%a %Y-%m-%d") for day in days)


class Extractor:
    def __init__(self, api_key, models, tz_name, timetable, retry_waits=RETRY_WAITS):
        """models: best first. The free tier has a small daily quota per model, so when one
        runs out the next one takes over.
        retry_waits: seconds to wait between tries when the service is busy."""
        self.retry_waits = tuple(retry_waits)
        self.used_up = []  # models found to be out of daily quota during this run
        self.client = genai.Client(api_key=api_key)
        self.models = [m.strip() for m in models if m.strip()]
        self.tz = ZoneInfo(tz_name)
        self.system = RULES.format(timezone=tz_name, timetable=timetable)

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
        config = types.GenerateContentConfig(
            system_instruction=self.system,
            response_mime_type="application/json",
            response_schema=Decision,
            temperature=0,
        )
        waits = list(self.retry_waits)
        while True:
            try:
                return self.client.models.generate_content(
                    model=self.models[0], contents=prompt, config=config
                ).text
            except errors.APIError as error:
                daily_quota = error.code == 429 and "PerDay" in str(error)
                if (daily_quota or error.code == 404) and len(self.models) > 1:
                    # Used up for today, or the model no longer exists: move to the next one.
                    print(f"   ({self.models[0]} unavailable, switching to {self.models[1]})")
                    if daily_quota:
                        self.used_up.append(self.models[0])
                    self.models.pop(0)
                    waits = list(self.retry_waits)
                elif error.code in (429, 500, 503) and waits and not daily_quota:
                    time.sleep(waits.pop(0))
                else:
                    raise AIUnavailable(f"AI service error {error.code}", quota=daily_quota) from None
