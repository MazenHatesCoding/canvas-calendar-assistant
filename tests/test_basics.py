"""Checks for the parts that need no network. Run:  python -m unittest discover tests"""

import os
import time
import unittest
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from google.genai import errors

from assistant import core, extractor as ex, telegram as tg
from assistant.canvas import html_to_text, parse_time
from assistant.gcal import describe, merge_changes, summary_for, when_fields
from assistant import config
from assistant.web import same_secret

TZ = ZoneInfo("Africa/Cairo")


def event(title, start, end, kind="quiz"):
    def side(value):
        return {"date": value} if len(value) == 10 else {"dateTime": value + ":00+02:00"}

    return {
        "summary": title,
        "start": side(start),
        "end": side(end),
        "extendedProperties": {"private": {"title": title, "kind": kind}},
    }


class CanvasText(unittest.TestCase):
    def test_html_becomes_lines(self):
        html = "<p>Quiz 2 <b>next Tuesday</b></p><ul><li>Lab 3</li><li>10 AM</li></ul>"
        self.assertEqual(html_to_text(html), "Quiz 2 next Tuesday\nLab 3\n10 AM")

    def test_empty_html(self):
        self.assertEqual(html_to_text(None), "")

    def test_parse_time(self):
        self.assertEqual(parse_time("2026-11-09T09:00:06Z").hour, 9)
        self.assertIsNone(parse_time(None))
        self.assertIsNone(parse_time("soon"))


class CalendarFields(unittest.TestCase):
    def test_timed_event(self):
        start, end, dated = when_fields("2026-11-18T14:00", "2026-11-18T15:40", False, date(2026, 11, 9), "Africa/Cairo")
        self.assertEqual(start["dateTime"], "2026-11-18T14:00:00")
        self.assertEqual(end["dateTime"], "2026-11-18T15:40:00")
        self.assertTrue(dated)

    def test_missing_or_backwards_end_gets_an_hour(self):
        for bad_end in (None, "nonsense", "2026-11-18T13:00"):
            _, end, _ = when_fields("2026-11-18T14:00", bad_end, False, date(2026, 11, 9), "Africa/Cairo")
            self.assertEqual(end["dateTime"], "2026-11-18T15:00:00")

    def test_all_day(self):
        start, end, dated = when_fields("2026-11-18", None, True, date(2026, 11, 9), "Africa/Cairo")
        self.assertEqual((start, end, dated), ({"date": "2026-11-18"}, {"date": "2026-11-19"}, True))

    def test_no_date_falls_back_to_posting_day(self):
        for missing in (None, "", "next week sometime"):
            start, _, dated = when_fields(missing, None, False, date(2026, 11, 9), "Africa/Cairo")
            self.assertEqual(start, {"date": "2026-11-09"})
            self.assertFalse(dated)

    def test_summary_prefixes(self):
        self.assertEqual(summary_for("DB: Quiz", False), "DB: Quiz")
        self.assertEqual(summary_for("DB: Quiz", True), "[?] DB: Quiz")
        self.assertEqual(summary_for("DB: Quiz", True, dated=False), "[date unknown] DB: Quiz")

    def test_describe(self):
        shown = describe(event("DB: Quiz", "2026-11-18T14:00", "2026-11-18T15:40"))
        self.assertEqual(shown, {"title": "DB: Quiz", "start": "2026-11-18T14:00", "end": "2026-11-18T15:40"})


class TelegramWording(unittest.TestCase):
    NOW = datetime(2026, 11, 11, 9, 0, tzinfo=TZ)

    def test_digest_covers_the_whole_seventh_day(self):
        events = [
            event("SE: Deliverable", "2026-11-14T23:29", "2026-11-14T23:59", kind="project"),
            event("DB: Quiz", "2026-11-18T14:00", "2026-11-18T15:40"),  # day 7, after 09:00
            event("Too far", "2026-11-19T10:00", "2026-11-19T11:00"),
            event("Already over", "2026-11-10T10:00", "2026-11-10T11:00"),
        ]
        text = tg.digest_text(events, self.NOW, TZ)
        self.assertIn("due 23:59  SE: Deliverable", text)
        self.assertIn("14:00  DB: Quiz", text)
        self.assertNotIn("Too far", text)
        self.assertNotIn("Already over", text)

    def test_digest_empty(self):
        self.assertIsNone(tg.digest_text([], self.NOW, TZ))

    def test_digest_names_today_and_tomorrow(self):
        events = [
            event("A", "2026-11-11T14:00", "2026-11-11T15:00"),
            event("B", "2026-11-12", "2026-11-13"),
        ]
        text = tg.digest_text(events, self.NOW, TZ)
        self.assertIn("<b>Today</b>\n• 14:00  A", text)
        self.assertIn("<b>Tomorrow</b>\n• all day  B", text)

    def test_when_text(self):
        self.assertEqual(tg.when_text(event("Q", "2026-11-18T14:00", "2026-11-18T15:40"), TZ), "Wed 18 Nov, 14:00–15:40")
        deadline = event("D", "2026-11-14T23:29", "2026-11-14T23:59", kind="assignment")
        self.assertEqual(tg.when_text(deadline, TZ), "Due Sat 14 Nov, 23:59")

    def test_titles_are_escaped(self):
        text = tg.digest_text([event("<b>x</b> & y", "2026-11-11T14:00", "2026-11-11T15:00")], self.NOW, TZ)
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; y", text)

    def test_week_requests(self):
        for text in ("/week", "/week@MyBot", "week", "Wrap up", "wrap-up!", "next 7 days", "digest"):
            self.assertTrue(tg.wants_week(text), text)
        for text in ("quiz next week", "wrap up the report by friday", "/help", "db quiz in 7 days"):
            self.assertFalse(tg.wants_week(text), text)

    def test_check_requests(self):
        for text in ("/check", "/check@MyBot", "check", "Check canvas", "refresh now!"):
            self.assertTrue(tg.wants_check(text), text)
        for text in ("check the quiz date", "/week", "check canvas for the db quiz"):
            self.assertFalse(tg.wants_check(text), text)

    def test_check_answer(self):
        self.assertEqual(tg.check_text({"new": 0, "waiting": 0, "outcomes": {}}), "✅ Checked Canvas: nothing new.")
        text = tg.check_text({"new": 3, "waiting": 1, "outcomes": {"created": 1, "ignored": 1}})
        self.assertIn("3 new posts (1 added, 1 nothing to do).", text)
        self.assertIn("1 still to go through", text)


class SavedState(unittest.TestCase):
    """Two requests loaded the same state; each one's changes must survive the other's save."""

    BASE = {"done": ["a1"], "token": {"created": "2026-11-11"}, "checking": 5}

    def test_both_sides_keep_their_changes(self):
        mine = {"done": ["a1", "a2"], "token": {"created": "2026-11-11", "sent": "2026-12-06"}}
        theirs = {**self.BASE, "token": {"created": "2026-11-11", "done": True}, "tg_seen": [7]}
        self.assertEqual(
            merge_changes(self.BASE, mine, theirs),
            {
                "done": ["a1", "a2"],
                "token": {"created": "2026-11-11", "sent": "2026-12-06", "done": True},
                "tg_seen": [7],
            },
        )

    def test_untouched_values_come_from_the_stored_copy(self):
        theirs = {**self.BASE, "done": ["a1", "a9"]}
        self.assertEqual(merge_changes(self.BASE, dict(self.BASE), theirs), theirs)

    def test_inputs_are_not_modified(self):
        mine, theirs = {"token": {"sent": "x"}}, {"token": {"done": True}}
        merge_changes({}, mine, theirs)
        self.assertEqual((mine, theirs), ({"token": {"sent": "x"}}, {"token": {"done": True}}))


class WebSecrets(unittest.TestCase):
    def test_matching_secret(self):
        self.assertTrue(same_secret("s3cret-value", "s3cret-value"))

    def test_wrong_or_missing_secret(self):
        for given in (None, "", "nope", "s3cret-value "):
            self.assertFalse(same_secret(given, "s3cret-value"), given)

    def test_unset_secret_never_matches(self):
        self.assertFalse(same_secret("", ""))
        self.assertFalse(same_secret(None, ""))

    def test_webhook_secret_comes_from_the_bot_token(self):
        self.addCleanup(os.environ.pop, "TELEGRAM_BOT_TOKEN", None)
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
        self.assertEqual(config.webhook_secret(), "")
        os.environ["TELEGRAM_BOT_TOKEN"] = "123:abc"
        first = config.webhook_secret()
        self.assertRegex(first, r"^[0-9a-f]{64}$")  # only characters Telegram accepts
        self.assertNotIn("123:abc", first)
        os.environ["TELEGRAM_BOT_TOKEN"] = "123:abd"
        self.assertNotEqual(config.webhook_secret(), first)


class GoogleConnection(unittest.TestCase):
    def setUp(self):
        for name in ("GOOGLE_TOKEN_JSON", "GOOGLE_CALENDAR_ID"):
            self.addCleanup(os.environ.__setitem__, name, os.environ.get(name, ""))
        os.environ["GOOGLE_CALENDAR_ID"] = ""

    def test_calendar_id_can_live_inside_the_sign_in(self):
        os.environ["GOOGLE_TOKEN_JSON"] = '{"refresh_token": "r", "calendar_id": "abc@group"}'
        self.assertEqual(config.calendar_id(), "abc@group")
        os.environ["GOOGLE_CALENDAR_ID"] = "other@group"
        self.assertEqual(config.calendar_id(), "other@group")

    def test_broken_value_counts_as_missing(self):
        os.environ["GOOGLE_TOKEN_JSON"] = "not json"
        self.assertEqual(config.google_connection(), {})
        self.assertIn("GOOGLE_TOKEN_JSON", config.missing())


class Clock:
    """Stands in for the `time` module: waiting costs nothing and is counted instead."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


IGNORE = '{"reason": "nothing to do", "action": "ignore"}'


class FakeGemini:
    """behaviour: model name -> "hang" (never answers), "slow:N" (answers after N seconds),
    "quota" (daily quota used up) or "busy". Any other model answers at once."""

    def __init__(self, clock, behaviour):
        self.clock, self.behaviour, self.calls = clock, behaviour, []
        self.models = self

    def generate_content(self, model, contents, config):
        timeout = config.http_options.timeout / 1000
        self.calls.append((model, timeout))
        how = self.behaviour.get(model, "")
        needs = float("inf") if how == "hang" else float(how[5:]) if how.startswith("slow:") else 0
        if needs > timeout:
            self.clock.sleep(timeout)
            raise TimeoutError()
        self.clock.sleep(needs)
        if how == "quota":
            raise errors.APIError(429, {"error": {"message": "GenerateRequestsPerDayPerProjectPerModel"}})
        if how == "busy":
            raise errors.APIError(503, {"error": {"message": "overloaded"}})
        return mock.Mock(text=IGNORE)


MODELS = ["best", "good", "fair", "lite"]


class ModelFallback(unittest.TestCase):
    def ask(self, behaviour, **settings):
        clock = Clock()
        gemini = FakeGemini(clock, behaviour)
        with mock.patch.object(ex.Extractor, "_client", staticmethod(lambda key: gemini)):
            extractor = ex.Extractor("key", MODELS, "Africa/Cairo", "", **settings)
        with mock.patch.object(ex, "time", clock):
            try:
                answer = extractor._ask("prompt")
            except ex.AIUnavailable as error:
                answer = error
        return answer, extractor, gemini, clock

    def test_someone_waiting_skips_past_hanging_models(self):
        """What went wrong on 11 Nov 2026: two models hung and used up the whole 40 seconds."""
        answer, extractor, gemini, clock = self.ask({"best": "hang", "good": "hang"}, **core.QUICK_AI)
        self.assertEqual(answer, IGNORE)
        self.assertEqual([model for model, _ in gemini.calls], ["best", "lite"])
        self.assertLessEqual(clock.now, core.QUICK_AI["give_up_after"])
        self.assertEqual(extractor.stalled, ["best"])

    def test_last_model_gets_all_the_time_left(self):
        answer, _, gemini, clock = self.ask({"best": "hang", "lite": "slow:25"}, **core.QUICK_AI)
        self.assertEqual(answer, IGNORE)
        self.assertEqual(gemini.calls[-1], ("lite", 28))
        self.assertLessEqual(clock.now, core.QUICK_AI["give_up_after"])

    def test_busy_models_do_not_use_up_the_reserve(self):
        answer, extractor, gemini, _ = self.ask({"best": "busy"}, **core.QUICK_AI)
        self.assertEqual(answer, IGNORE)
        self.assertEqual([model for model, _ in gemini.calls], ["best"] * 3 + ["lite"])
        self.assertEqual((extractor.stalled, extractor.out_of_quota), ([], []))

    def test_everything_hanging_gives_up_in_time(self):
        answer, extractor, _, clock = self.ask({m: "hang" for m in MODELS}, **core.QUICK_AI)
        self.assertIsInstance(answer, ex.AIUnavailable)
        self.assertLessEqual(clock.now, core.QUICK_AI["give_up_after"])
        self.assertEqual(extractor.stalled, ["best", "lite"])

    def test_a_model_cut_short_is_not_blamed(self):
        settings = {"retry_waits": (), "call_timeout": 30, "give_up_after": 40, "reserve": 15}
        answer, extractor, gemini, _ = self.ask({"best": "slow:28"}, **settings)
        self.assertEqual(answer, IGNORE)
        self.assertEqual(gemini.calls, [("best", 25), ("lite", 15)])
        self.assertEqual(extractor.stalled, [])

    def test_quota_moves_to_the_next_model(self):
        answer, extractor, gemini, _ = self.ask({"best": "quota"}, **core.QUICK_AI)
        self.assertEqual(answer, IGNORE)
        self.assertEqual([model for model, _ in gemini.calls], ["best", "good"])
        self.assertEqual(extractor.out_of_quota, ["best"])
        self.assertEqual(extractor.answered, {"good"})

    def test_hourly_check_tries_every_model_in_order(self):
        answer, extractor, gemini, _ = self.ask({"best": "hang", "good": "hang"})
        self.assertEqual(answer, IGNORE)
        self.assertEqual(gemini.calls, [("best", 60), ("good", 60), ("fair", 60)])
        self.assertEqual(extractor.stalled, ["best", "good"])


class FakeCalendar:
    def save_state(self, state):
        pass


def assistant_with(state, quick=False):
    """An Assistant with no network behind it, and a stand-in for the AI."""
    a = core.Assistant.__new__(core.Assistant)
    a.verbose, a.tz, a.tz_name = False, TZ, "Africa/Cairo"
    a.now = datetime(2026, 11, 11, 9, 0, tzinfo=TZ)
    a.today, a.lookback_days = "2026-11-11", 21
    a.calendar, a.state, a.events = FakeCalendar(), state, []
    a.telegram, a.sent = object(), []
    a.tell = lambda text, buttons=None: a.sent.append(text) or True
    a.last_check = {"new": 0, "waiting": 0, "outcomes": {}}
    a._quick = quick
    a._extractor = mock.Mock(out_of_quota=[], stalled=[], answered=set())
    state.setdefault("ai_out", {})
    state.setdefault("ai_slow", {})
    return a


NOTE = {"text": "graph theory quiz next Monday", "date": 1794380400}


class SavedNotes(unittest.TestCase):
    def test_note_is_kept_when_the_ai_cannot_answer(self):
        a = assistant_with({}, quick=True)
        a._extractor.decide.side_effect = ex.AIUnavailable("AI service too slow")
        a.add_by_hand(77, NOTE)
        self.assertEqual(a.state["notes"], {"77": NOTE})
        self.assertIn("saved your note", a.sent[0])
        self.assertNotIn("send that again", a.sent[0])

    def test_no_room_left_asks_to_send_again(self):
        a = assistant_with({}, quick=True)
        a._extractor.decide.side_effect = ex.AIUnavailable("AI service too slow")
        a.add_by_hand(77, {"text": "x" * core.NOTES_ROOM, "date": 1})
        self.assertNotIn("notes", a.state)
        self.assertIn("send that again", a.sent[0])

    def test_next_check_handles_the_note_and_forgets_it(self):
        a = assistant_with({"notes": {"77": NOTE}})
        a._extractor.decide.return_value = ex.Decision(reason="it is a greeting", action="ignore")
        a.handle_notes(time.monotonic(), 150)
        self.assertEqual(a.state["notes"], {})
        self.assertEqual(a._extractor.decide.call_args.args[0].id, "manual-77")
        self.assertIn("graph theory quiz next Monday", a.sent[0])
        self.assertIn("it is a greeting", a.sent[0])

    def test_note_keeps_waiting_while_the_ai_is_down(self):
        a = assistant_with({"notes": {"77": NOTE, "78": NOTE}})
        a._extractor.decide.side_effect = ex.AIUnavailable("quota", quota=True)
        a.handle_notes(time.monotonic(), 150)
        self.assertEqual(set(a.state["notes"]), {"77", "78"})
        self.assertEqual(a.sent, [])
        self.assertEqual(a._extractor.decide.call_count, 1)

    def test_note_already_in_the_calendar_is_not_added_twice(self):
        a = assistant_with({"notes": {"77": NOTE}})
        a.events = [{"extendedProperties": {"private": {"source": "manual-77"}}}]
        a.handle_notes(time.monotonic(), 150)
        self.assertEqual(a.state["notes"], {})
        a._extractor.decide.assert_not_called()


class ModelMemory(unittest.TestCase):
    def remember(self, quick, **found):
        a = assistant_with({"ai_out": {"old": 5}, "ai_slow": {"good": 5}}, quick=quick)
        a._extractor = mock.Mock(**{"out_of_quota": [], "stalled": [], "answered": set(), **found})
        a._extractor.decide.return_value = ex.Decision(reason="r", action="ignore")
        a.apply(a.note_item(1, "hello", 1794380400), manual=True)
        return a.state

    def test_stall_while_someone_waits_only_slows_typed_messages(self):
        state = self.remember(quick=True, stalled=["best"])
        self.assertEqual((set(state["ai_out"]), set(state["ai_slow"])), ({"old"}, {"good", "best"}))

    def test_stall_in_the_hourly_check_and_quota_rule_a_model_out(self):
        state = self.remember(quick=False, stalled=["best"], out_of_quota=["fair"])
        self.assertEqual(set(state["ai_out"]), {"old", "best", "fair"})

    def test_an_answer_brings_a_model_back(self):
        state = self.remember(quick=False, answered={"good", "old"})
        self.assertEqual((state["ai_out"], state["ai_slow"]), ({}, {}))

    def test_typed_messages_skip_slow_models_but_the_hourly_check_does_not(self):
        fresh = int(time.time())
        settings = {"GEMINI_MODELS": ",".join(MODELS), "TIMETABLE": "x"}
        for quick, expected in ((True, ["fair", "lite"]), (False, ["good", "fair", "lite"])):
            a = assistant_with({"ai_out": {"best": fresh}, "ai_slow": {"good": fresh, "fair": 5}}, quick=quick)
            a._extractor = None
            with mock.patch.object(core, "Extractor") as made, mock.patch.dict(os.environ, settings):
                a.extractor()
            self.assertEqual(made.call_args.args[1], expected)
            self.assertEqual(a.state["ai_slow"], {"good": fresh})  # the stale entry is dropped


if __name__ == "__main__":
    unittest.main()
