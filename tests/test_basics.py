"""Checks for the parts that need no network. Run:  python -m unittest discover tests"""

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from assistant import telegram as tg
from assistant.canvas import html_to_text, parse_time
from assistant.gcal import describe, summary_for, when_fields

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


if __name__ == "__main__":
    unittest.main()
