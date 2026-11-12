"""Whole checks and bot conversations, run against stand-ins for every outside service."""

import time
import unittest
from datetime import datetime, timedelta

import requests

from assistant import core, extractor as ex
from assistant.canvas import CanvasProblem
from fakes import AI, Canvas, OWNER, TZ, Telegram, button_update, decision, post, said, text_update, world

LINKED = {"chat_id": OWNER}


def check(**kwargs):
    return core.Assistant().check_canvas(**kwargs)


def send(update):
    core.Assistant().handle_update(update)


class HourlyCheck(unittest.TestCase):
    def test_new_post_becomes_an_event_once(self):
        with world(LINKED) as google:
            Canvas.items = [post(1)]
            AI.answers = {"announcement-1": decision()}
            self.assertEqual(check(), "ok")
            self.assertEqual(google.titles(), ["Databases: Quiz"])
            self.assertEqual(len(said()), 1)
            self.assertIn("Added", said()[0])
            self.assertEqual(check(), "ok")
            self.assertEqual(AI.asked, ["announcement-1"])  # not sent to the AI twice
            self.assertEqual(len(google.real_events()), 1)
            self.assertNotIn("checking", google.state())

    def test_past_events_are_written_quietly(self):
        with world(LINKED) as google:
            Canvas.items = [post(1, hours_ago=200)]
            AI.answers = {"announcement-1": decision(days=-5)}
            check()
            self.assertEqual(len(google.real_events()), 1)
            self.assertEqual(said(), [])

    def test_move_cancel_and_note(self):
        with world(LINKED) as google:
            Canvas.items = [post(1, hours_ago=30)]
            AI.answers = {"announcement-1": decision(start=None, all_day=True, title="Databases: Quiz", kind="quiz")}
            check()
            (event,) = google.real_events()
            self.assertIn("date", event["start"])
            self.assertTrue(event["summary"].startswith("[date unknown]"))

            Canvas.items.append(post(2, "Quiz moved", hours_ago=20))
            AI.answers["announcement-2"] = decision("update", days=4, target_key="E1", location="Room 200")
            check()
            (event,) = google.real_events()
            self.assertEqual(set(event["start"]), {"dateTime", "timeZone"})  # the all-day date is gone
            self.assertEqual(event["summary"], "Databases: Quiz")
            self.assertEqual(event["location"], "Room 200")
            self.assertIn("Changed", said()[-1])

            Canvas.items.append(post(3, "Scope", hours_ago=10))
            AI.answers["announcement-3"] = decision("note", target_key="E1", notes="Chapters 1-3")
            check()
            self.assertIn("Chapters 1-3", google.real_events()[0]["description"])
            self.assertIn("New details", said()[-1])

            Canvas.items.append(post(4, "Cancelled", hours_ago=1))
            AI.answers["announcement-4"] = decision("update", target_key="E1", cancelled=True)
            check()
            self.assertEqual(google.real_events(), [])
            self.assertIn("Cancelled", said()[-1])

    def test_ai_down_keeps_the_rest_for_later(self):
        with world(LINKED) as google:
            Canvas.items = [post(1, hours_ago=3), post(2, hours_ago=2), post(3, hours_ago=1)]
            AI.answers = {
                "announcement-1": decision(),
                "announcement-2": ex.AIUnavailable("AI service error 503"),
            }
            self.assertEqual(check(), "ok")
            self.assertEqual(google.state()["done"], ["a1"])
            AI.answers["announcement-2"] = decision(title="Databases: Lab task")
            check()
            self.assertEqual(google.state()["done"], ["a1", "a2", "a3"])
            self.assertEqual(AI.asked.count("announcement-1"), 1)

    def test_unreadable_post_is_handed_to_the_student(self):
        with world(LINKED) as google:
            Canvas.items = [post(1, "Odd <post>")]
            AI.answers = {"announcement-1": ex.Unreadable("no")}
            check()
            self.assertIn("Odd &lt;post&gt;", said()[0])
            self.assertEqual(google.state()["done"], ["a1"])

    def test_canvas_problem_is_reported_once_a_day(self):
        with world(LINKED):
            Canvas.problem = CanvasProblem("Canvas rejected the access token.")
            self.assertEqual(check(), "ok")
            self.assertEqual(check(), "ok")
            self.assertEqual(len(said()), 1)
            self.assertIn("rejected", said()[0])

    def test_canvas_problem_nobody_could_be_told_about_fails(self):
        with world({}):  # no chat linked yet
            Canvas.problem = CanvasProblem("Could not reach Canvas.")
            self.assertEqual(check(), "failed")

    def test_lost_state_does_not_duplicate_events(self):
        with world(LINKED) as google:
            Canvas.items = [post(1)]
            AI.answers = {"announcement-1": decision()}
            check()
            google.set_state(LINKED)  # the list of finished posts is gone
            check()
            self.assertEqual(len(google.real_events()), 1)
            self.assertEqual(AI.asked, ["announcement-1"])

    def test_second_check_waits_while_one_is_running(self):
        with world({**LINKED, "checking": int(time.time()) - 30}):
            self.assertEqual(check(), "busy")
        with world({**LINKED, "checking": int(time.time()) - core.CHECK_LOCK - 5}):
            self.assertEqual(check(), "ok")  # the earlier one was stopped by the host

    def test_out_of_time_leaves_the_rest(self):
        with world(LINKED) as google:
            Canvas.items = [post(1, hours_ago=2), post(2, hours_ago=1)]
            check(budget=-1)
            self.assertEqual(google.state()["done"], [])
            self.assertEqual(AI.asked, [])

    def test_calendar_refusing_one_post_does_not_stop_everything_silently(self):
        """A post Google will not accept must not hold up the check forever without a word."""
        with world(LINKED) as google:
            Canvas.items = [post(1, hours_ago=2), post(2, hours_ago=1)]
            AI.answers = {"announcement-1": decision(), "announcement-2": decision(title="Databases: Exam")}
            google.fail = ("POST", 400)
            outcome = check()
            self.assertEqual(outcome, "ok")
            self.assertEqual(len(said()), 1)
            self.assertIn("Google Calendar", said()[0])
            self.assertNotIn("checking", google.state())
            google.fail = None
            check()
            self.assertEqual(len(google.real_events()), 2)

    def test_update_without_a_matching_event_is_not_dropped(self):
        with world(LINKED) as google:
            Canvas.items = [post(1, "Quiz moved")]
            AI.answers = {"announcement-1": decision("update", target_key="E9")}
            check()
            self.assertEqual(google.titles(), ["Databases: Quiz"])
            self.assertIn("Added", said()[0])

    def test_notice_without_event(self):
        with world(LINKED) as google:
            Canvas.items = [post(1, "No lecture", hours_ago=2), post(2, "No lecture", hours_ago=100)]
            AI.answers = {
                "announcement-1": decision("notify", notes="The Monday lecture is cancelled."),
                "announcement-2": decision("notify", notes="Old news."),
            }
            check()
            self.assertEqual(google.real_events(), [])
            self.assertEqual(len(said()), 1)  # the four-day-old one is not sent
            self.assertIn("The Monday lecture is cancelled.", said()[0])

    def test_telegram_down_does_not_break_the_check(self):
        with world(LINKED) as google:
            Telegram.broken = True
            Canvas.items = [post(1)]
            AI.answers = {"announcement-1": decision()}
            self.assertEqual(check(), "ok")
            self.assertEqual(len(google.real_events()), 1)

    def test_network_trouble_with_google_is_an_ordinary_failure(self):
        with world(LINKED) as google:
            Canvas.items = [post(1)]
            AI.answers = {"announcement-1": decision()}
            google.fail = ("POST", requests.ConnectionError("down"))
            self.assertEqual(check(), "ok")
            self.assertEqual(google.state().get("done"), [])


class SavedStateRaces(unittest.TestCase):
    def test_message_during_a_check_loses_nothing(self):
        with world(LINKED) as google:
            Canvas.items = [post(1)]
            AI.answers = {"announcement-1": decision()}
            checker = core.Assistant()

            def message_arrives():
                send(text_update(500, "/week"))

            original = checker.handle_canvas

            def handle_canvas(started, budget):
                original(started, budget)
                message_arrives()

            checker.handle_canvas = handle_canvas
            checker.check_canvas()
            state = google.state()
            self.assertEqual(state["done"], ["a1"])
            self.assertEqual(state["tg_seen"], [500])
            self.assertNotIn("checking", state)
            self.assertNotIn("tg_open", state)

    def test_write_that_loses_the_race_is_retried(self):
        with world(LINKED) as google:
            first = core.Assistant()
            first.state["digest"] = "2026-01-01"

            def someone_else_saves():
                other = core.Assistant()
                other.state["alerts"] = {"ai": "2026-01-01"}
                other.save()

            google.before_write = someone_else_saves
            first.save()
            state = google.state()
            self.assertEqual((state["digest"], state["alerts"]), ("2026-01-01", {"ai": "2026-01-01"}))

    def test_two_messages_at_once_are_both_remembered(self):
        with world(LINKED) as google:
            first, second = core.Assistant(), core.Assistant()
            first.handle_update(text_update(1, "/help"))
            second.handle_update(text_update(2, "/help"))
            self.assertEqual(sorted(google.state()["tg_seen"]), [1, 2])


class Bot(unittest.TestCase):
    def test_first_message_links_the_chat(self):
        with world({}) as google:
            send(text_update(1, "/start"))
            self.assertEqual(google.state()["chat_id"], OWNER)
            self.assertIn("Linked", said()[0])
            send(text_update(2, "hello", chat=999))  # a stranger later on
            self.assertEqual(len(said()), 1)
            self.assertEqual(AI.asked, [])

    def test_repeated_delivery_is_handled_once(self):
        with world(LINKED) as google:
            AI.answers = {"manual-7": decision()}
            send(text_update(7, "db quiz in three days at 2"))
            send(text_update(7, "db quiz in three days at 2"))
            self.assertEqual(len(google.real_events()), 1)
            self.assertEqual(len(said()), 1)

    def test_delivery_repeated_after_a_cut_off_says_so(self):
        with world({**LINKED, "tg_seen": [7], "tg_open": 7}) as google:
            send(text_update(7, "db quiz in three days at 2"))
            self.assertIn("took too long", said()[0])
            self.assertNotIn("tg_open", google.state())

    def test_typed_note_add_change_cancel(self):
        with world(LINKED) as google:
            AI.answers = {
                "manual-1": decision(),
                "manual-2": decision("update", days=5, target_key="E1"),
                "manual-3": decision("update", target_key="E1", cancelled=True),
            }
            send(text_update(1, "db quiz in three days at 2"))
            self.assertIn("Added", said()[-1])
            self.assertNotIn("Open in Canvas", said()[-1])
            send(text_update(2, "moved two days later"))
            self.assertIn("Changed", said()[-1])
            send(text_update(3, "cancel it"))
            self.assertIn("Cancelled", said()[-1])
            self.assertEqual(google.real_events(), [])

    def test_week_and_help_need_no_ai(self):
        with world(LINKED):
            send(text_update(1, "/week"))
            send(text_update(2, "/help"))
            send(text_update(3, "/whatever"))
            self.assertEqual(len(said()), 3)
            self.assertIn("Nothing in your calendar", said()[0])
            self.assertEqual(AI.asked, [])

    def test_every_message_from_the_owner_gets_an_answer(self):
        with world(LINKED):
            update = text_update(1, "")
            update["message"].pop("text")
            update["message"]["photo"] = [{"file_id": "x"}]
            send(update)
            self.assertEqual(len(said()), 1)

    def test_groups_and_strangers_get_nothing(self):
        with world(LINKED):
            send(text_update(1, "hello", chat=-5, chat_type="group"))
            send(text_update(2, "hello", chat=777))
            send(button_update(3, "token_done", chat=777))
            self.assertEqual(said(), [])

    def test_check_on_request_reports(self):
        with world(LINKED):
            Canvas.items = [post(1)]
            AI.answers = {"announcement-1": decision()}
            send(text_update(1, "/check"))
            self.assertIn("Added", said()[0])
            self.assertIn("1 new post (1 added)", said()[1])
            send(text_update(2, "check"))
            self.assertIn("nothing new", said()[2])

    def test_check_on_request_never_says_all_is_well_when_canvas_failed(self):
        with world(LINKED):
            Canvas.problem = CanvasProblem("Canvas rejected the access token.")
            send(text_update(1, "/check"))
            send(text_update(2, "/check"))
            self.assertEqual(len(said()), 2)  # asked twice, told twice
            for text in said():
                self.assertIn("rejected", text)
                self.assertNotIn("nothing new", text)

    def test_check_on_request_while_one_is_running(self):
        with world({**LINKED, "checking": int(time.time()) - 5}):
            send(text_update(1, "/check"))
            self.assertIn("already running", said()[0])

    def test_calendar_trouble_while_adding_is_answered(self):
        for failure in (500, requests.ConnectionError("down"), requests.Timeout("slow")):
            with world(LINKED) as google:
                AI.answers = {"manual-1": decision()}
                reader = core.Assistant()
                google.fail = ("POST", failure)
                reader.handle_update(text_update(1, "db quiz in three days at 2"))
                self.assertEqual(len(said()), 1, failure)
                self.assertIn("couldn't save", said()[0])

    def test_change_to_something_not_in_the_calendar(self):
        with world(LINKED) as google:
            AI.answers = {"manual-1": decision("update", target_key=None, cancelled=True)}
            send(text_update(1, "cancel the physics lab"))
            self.assertEqual(len(said()), 1)
            self.assertIn("couldn't find", said()[0])
            self.assertEqual(google.real_events(), [])

    def test_saved_note_is_handled_by_the_next_check(self):
        with world(LINKED) as google:
            AI.answers = {"manual-1": ex.AIUnavailable("AI service too slow")}
            send(text_update(1, "db quiz in three days at 2"))
            self.assertIn("saved your note", said()[0])
            self.assertIn("1", google.state()["notes"])
            AI.answers = {"manual-1": decision()}
            check()
            self.assertEqual(google.state()["notes"], {})
            self.assertEqual(google.titles(), ["Databases: Quiz"])
            self.assertIn("Your saved note", said()[1])


class TokenReminder(unittest.TestCase):
    def run_on(self, google_world, age, hour=9):
        a = core.Assistant()
        created = datetime.fromisoformat(a.state.get("token", {}).get("created") or "2026-11-11")
        a.now = (created + timedelta(days=age)).replace(hour=hour, tzinfo=TZ)
        a.today = a.now.date().isoformat()
        a.check_canvas()

    def test_reminds_daily_from_day_25_until_done(self):
        with world(LINKED, CANVAS_TOKEN_CREATED="2026-11-11") as google:
            self.run_on(google, 24)
            self.assertEqual(said(), [])
            self.run_on(google, 25, hour=7)  # too early in the day
            self.assertEqual(said(), [])
            self.run_on(google, 25)
            self.run_on(google, 25, hour=15)
            self.assertEqual(len(said()), 1)
            self.assertIn("expires in 5 days", said()[0])
            self.assertEqual(len(Telegram.sent[0][2]), 2)
            send(button_update(1, "token_snooze"))
            self.run_on(google, 26)
            self.assertIn("expires in 4 days", said()[-1])
            self.run_on(google, 31)
            self.assertIn("probably expired", said()[-1])
            send(button_update(2, "token_done"))
            count = len(said())
            self.run_on(google, 32)
            self.assertEqual(len(said()), count)

    def test_a_new_token_starts_the_count_again(self):
        """Renewing the token and pressing Done must not switch reminders off for good,
        even when the date setting was left as it was."""
        with world(LINKED, CANVAS_TOKEN_CREATED="2026-11-11") as google:
            self.run_on(google, 26)
            send(button_update(1, "token_done"))
            self.assertTrue(google.state()["token"]["done"])
        state = google.state()
        with world(state, CANVAS_TOKEN_CREATED="2026-11-11", CANVAS_TOKEN="a-new-token") as google:
            a = core.Assistant()
            a.now = datetime(2026, 12, 8, 9, 0, tzinfo=TZ)
            a.today = "2026-12-08"
            a.check_canvas()
            self.assertEqual(said(), [])
            a = core.Assistant()
            a.now = datetime(2027, 1, 3, 9, 0, tzinfo=TZ)  # 26 days after the new token
            a.today = "2027-01-03"
            a.check_canvas()
            self.assertEqual(len(said()), 1)
            self.assertIn("expires in 4 days", said()[0])

    def test_counts_from_first_use_when_no_date_is_given(self):
        with world(LINKED) as google:
            check()
            day = google.state()["token_seen"]["day"]
            self.assertEqual(day, datetime.now(TZ).date().isoformat())


class Digest(unittest.TestCase):
    def test_once_a_day_after_the_hour(self):
        with world(LINKED, DIGEST_HOUR="7") as google:
            Canvas.items = [post(1, hours_ago=30)]
            AI.answers = {"announcement-1": decision()}
            a = core.Assistant()
            a.now = a.now.replace(hour=6)
            a.check_canvas()
            self.assertEqual(len(said()), 1)  # only the "Added" message
            for _ in range(2):
                a = core.Assistant()
                a.now = a.now.replace(hour=8)
                a.check_canvas()
            self.assertEqual(len(said()), 2)
            self.assertIn("Your next 7 days", said()[1])

    def test_bad_settings_do_not_stop_the_check(self):
        for name, value in (("DIGEST_HOUR", "99"), ("DIGEST_HOUR", "seven"), ("LOOKBACK_DAYS", "many"),
                            ("TELEGRAM_CHAT_ID", "me"), ("TIMEZONE", "Mars/Olympus")):
            with world(LINKED, **{name: value}):
                self.assertEqual(check(), "ok", name)


if __name__ == "__main__":
    unittest.main()
