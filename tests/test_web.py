"""The web addresses in api/, called the way Vercel and Telegram call them, and a
randomised hunt for ways the model fallback could overrun its time."""

import importlib.util
import io
import json
import os
import random
import unittest
from pathlib import Path
from unittest import mock

import requests
from google.genai import errors

from assistant import config, core, extractor as ex, gcal, telegram as tg
from fakes import AI, Canvas, OWNER, Telegram, decision, post, said, text_update, world

API = Path(__file__).resolve().parent.parent / "api"


def load(name):
    spec = importlib.util.spec_from_file_location(f"api_{name}", API / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(module, method="POST", body=b"", headers=None, path="/"):
    """Run one request through a handler. Returns (status, body text)."""
    headers = {"Content-Length": str(len(body)), **(headers or {})}
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"

    class Connection:
        def __init__(self):
            self.sent = io.BytesIO()

        def makefile(self, mode, *args, **kwargs):
            return io.BytesIO(head.encode() + body) if "r" in mode else self.sent

        def sendall(self, data):
            self.sent.write(data)

    connection = Connection()
    handler = module.handler.__new__(module.handler)
    handler.log_message = lambda *args: None
    out = io.BytesIO()
    handler.rfile, handler.wfile = io.BytesIO(head.encode() + body), out
    handler.client_address, handler.server = ("test", 0), None
    handler.handle_one_request()
    raw = out.getvalue().decode()
    return int(raw.split()[1]), raw.split("\r\n\r\n", 1)[1]


def telegram_call(module, update, secret=None):
    secret = config.webhook_secret() if secret is None else secret
    return call(module, body=json.dumps(update).encode(), headers={"X-Telegram-Bot-Api-Secret-Token": secret})


class TelegramAddress(unittest.TestCase):
    def setUp(self):
        self.api = load("telegram")

    def test_only_telegram_gets_in(self):
        with world({"chat_id": OWNER}):
            self.assertEqual(telegram_call(self.api, text_update(1, "/week"), secret="guess")[0], 401)
            self.assertEqual(telegram_call(self.api, text_update(1, "/week"), secret="")[0], 401)
            self.assertEqual(said(), [])
            self.assertEqual(telegram_call(self.api, text_update(1, "/week"))[0], 200)
            self.assertEqual(len(said()), 1)

    def test_nonsense_body(self):
        with world({"chat_id": OWNER}):
            headers = {"X-Telegram-Bot-Api-Secret-Token": config.webhook_secret()}
            self.assertEqual(call(self.api, body=b"not json", headers=headers)[0], 400)
            self.assertEqual(call(self.api, body=b"[1, 2]", headers=headers)[0], 400)
            self.assertEqual(call(self.api, body=b"{}", headers=headers)[0], 200)
            self.assertEqual(said(), [])

    def test_calendar_unreachable_still_answers(self):
        with world({"chat_id": OWNER}) as google:
            google.fail = ("GET", requests.ConnectionError("down"))
            self.assertEqual(telegram_call(self.api, text_update(1, "db quiz tomorrow"))[0], 200)
            self.assertEqual(len(said()), 1)
            self.assertIn("can't reach Google Calendar", said()[0])

    def test_dead_sign_in_is_explained(self):
        with world({"chat_id": OWNER}) as google:
            google.fail = ("GET", gcal.RefreshError("invalid_grant"))
            self.assertEqual(telegram_call(self.api, text_update(1, "/week"))[0], 200)
            self.assertIn("GOOGLE_TOKEN_JSON", said()[0])
            # Nothing is sent into a group, whoever asks.
            telegram_call(self.api, text_update(2, "/week", chat=-9, chat_type="group"))
            self.assertEqual(len(said()), 1)

    def test_failure_half_way_is_answered_and_not_repeated(self):
        with world({"chat_id": OWNER}) as google:
            AI.answers = {"manual-1": ValueError("something unforeseen")}
            self.assertEqual(telegram_call(self.api, text_update(1, "db quiz tomorrow"))[0], 200)
            self.assertEqual(len(said()), 1)
            self.assertIn("Something went wrong", said()[0])
            self.assertNotIn("tg_open", google.state())
            telegram_call(self.api, text_update(1, "db quiz tomorrow"))  # Telegram sends it again
            self.assertEqual(len(said()), 1)

    def test_missing_settings_are_ignored_quietly(self):
        with world({"chat_id": OWNER}, GEMINI_API_KEY=""):
            self.assertEqual(telegram_call(self.api, text_update(1, "/week")), (200, "ignored"))


class CheckAddress(unittest.TestCase):
    def setUp(self):
        self.api = load("check")

    def run_check(self, secret="s3cret", method="GET"):
        headers = {"Authorization": f"Bearer {secret}"} if secret is not None else {}
        return call(self.api, method=method, headers=headers)

    def test_needs_the_secret(self):
        with world({"chat_id": OWNER}, CRON_SECRET="s3cret"):
            Canvas.items = [post(1)]
            self.assertEqual(self.run_check(None)[0], 401)
            self.assertEqual(self.run_check("wrong")[0], 401)
            self.assertEqual(AI.asked, [])
            self.assertEqual(self.run_check(), (200, "ok"))
            self.assertEqual(self.run_check(method="POST"), (200, "ok"))
            self.assertEqual(AI.asked, ["announcement-1"])

    def test_no_secret_set_lets_nobody_in(self):
        with world({"chat_id": OWNER}, CRON_SECRET=""):
            self.assertEqual(self.run_check("")[0], 401)
            self.assertEqual(self.run_check(None)[0], 401)

    def test_missing_settings(self):
        with world({"chat_id": OWNER}, CRON_SECRET="s3cret", CANVAS_TOKEN=""):
            self.assertEqual(self.run_check()[0], 500)

    def test_busy_is_not_an_error(self):
        import time

        with world({"chat_id": OWNER, "checking": int(time.time())}, CRON_SECRET="s3cret"):
            self.assertEqual(self.run_check(), (200, "busy"))

    def test_dead_sign_in_is_reported_once_a_day_when_the_chat_is_known(self):
        with world({}, CRON_SECRET="s3cret", TELEGRAM_CHAT_ID=str(OWNER)) as google:
            google.fail = ("GET", gcal.RefreshError("invalid_grant"))
            for hour, expected in ((8, 0), (9, 1), (10, 1)):
                moment = mock.Mock(hour=hour)
                with mock.patch.object(self.api, "datetime", mock.Mock(now=lambda tz: moment)):
                    self.assertEqual(self.run_check()[0], 500)
                self.assertEqual(len(said()), expected)
            self.assertIn("GOOGLE_TOKEN_JSON", said()[0])


class SetupAddress(unittest.TestCase):
    def test_needs_the_secret_and_registers_the_lasting_address(self):
        api = load("setup")
        registered = []
        bot = mock.Mock()
        bot.me.return_value = {"username": "my_bot"}
        bot.set_webhook.side_effect = lambda url, secret: registered.append(url)
        with world({}, CRON_SECRET="s3cret", VERCEL_PROJECT_PRODUCTION_URL="mine.vercel.app", TIMEZONE="Nowhere"), \
                mock.patch.object(api, "check_canvas", lambda: (True, "ok")), \
                mock.patch.object(api, "check_ai", lambda: (True, "ok")), \
                mock.patch.object(api.tg, "Telegram", lambda token: bot):
            self.assertEqual(call(api, headers={"Authorization": "Bearer nope"})[0], 401)
            status, body = call(api, headers={"Authorization": "Bearer s3cret", "Host": "mine-abc123.vercel.app"})
            data = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(registered, ["https://mine.vercel.app/api/telegram"])
            self.assertFalse(data["ready"])  # the timezone is wrong, and the page says so
            self.assertEqual([c["name"] for c in data["checks"] if not c["ok"]], ["Optional settings"])


class SmallThings(unittest.TestCase):
    def test_long_message_is_split_between_lines(self):
        lines = [f"• 14:00  <b>Event {n}</b> " + "x" * 60 for n in range(200)]
        parts = tg.split_message("\n".join(lines))
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= tg.MESSAGE_LIMIT for part in parts))
        self.assertEqual("\n".join(parts).split("\n"), lines)
        self.assertEqual(tg.split_message("short"), ["short"])
        self.assertTrue(all(len(p) <= tg.MESSAGE_LIMIT for p in tg.split_message("y" * 9000)))

    def test_lists_in_the_saved_state_merge_entry_by_entry(self):
        merged = gcal.merge_changes({"done": ["a1", "a2"]}, {"done": ["a2", "a3"]}, {"done": ["a1", "a2", "a9"]})
        self.assertEqual(merged, {"done": ["a2", "a9", "a3"]})
        self.assertEqual(gcal.merge_changes({}, {"seen": [1]}, {"seen": [2]}), {"seen": [2, 1]})

    def test_post_google_keeps_rejecting_is_handed_over_after_three_checks(self):
        with world({"chat_id": OWNER}) as google:
            Canvas.items = [post(1, "Odd one", hours_ago=2), post(2, hours_ago=1)]
            AI.answers = {"announcement-1": decision(), "announcement-2": decision(title="Databases: Exam")}
            real = google.request

            def picky(method, url, **kwargs):
                if method == "POST" and (kwargs.get("json") or {}).get("summary") == "Databases: Quiz":
                    return mock.Mock(ok=False, status_code=400, text="Bad Request")
                return real(method, url, **kwargs)

            google.request = picky
            for _ in range(core.SAVE_TRIES):
                core.Assistant().check_canvas()
            self.assertEqual(google.titles(), ["Databases: Exam"])
            self.assertEqual(google.state()["done"], ["a1", "a2"])
            self.assertEqual(google.state().get("tries"), {})
            texts = said()
            self.assertEqual(sum("did not accept" in t for t in texts), 1)  # one warning a day
            self.assertEqual(sum("Odd one" in t and "read it yourself" in t for t in texts), 1)

    def test_an_outage_never_gives_up_on_a_post(self):
        with world({"chat_id": OWNER}) as google:
            Canvas.items = [post(1)]
            AI.answers = {"announcement-1": decision()}
            google.fail = ("POST", 503)
            for _ in range(core.SAVE_TRIES + 2):
                core.Assistant().check_canvas()
            self.assertEqual(google.state()["done"], [])
            google.fail = None
            core.Assistant().check_canvas()
            self.assertEqual(google.titles(), ["Databases: Quiz"])


class FallbackHunt(unittest.TestCase):
    """Random mixes of model behaviour. Whatever happens, a decision must end within its
    time, and must succeed when a model that answers quickly is there to be reached."""

    MODELS = ["m1", "m2", "m3", "m4", "m5"]

    class Clock:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    def gemini(self, clock, behaviour):
        calls = []

        def generate_content(model, contents, config):
            timeout = config.http_options.timeout / 1000
            calls.append((model, timeout))
            how = behaviour[model]
            self.assertGreaterEqual(timeout, ex.MIN_TIMEOUT)
            if how == "hang" or (how.startswith("slow") and float(how[4:]) > timeout):
                clock.sleep(timeout)
                raise TimeoutError()
            clock.sleep(float(how[4:]) if how.startswith("slow") else 0.5)
            if how == "quota":
                raise errors.APIError(429, {"error": {"message": "RequestsPerDayPerProjectPerModel"}})
            if how == "minute":
                raise errors.APIError(429, {"error": {"message": "RequestsPerMinute"}})
            if how in ("503", "500", "504", "404", "400", "403"):
                raise errors.APIError(int(how), {"error": {"message": "x"}})
            return mock.Mock(text="{}")

        return mock.Mock(models=mock.Mock(generate_content=generate_content)), calls

    def test_random_mixes(self):
        kinds = ["ok", "hang", "quota", "minute", "503", "500", "504", "404", "slow5", "slow20", "slow45", "slow100"]
        rng = random.Random(20261112)
        for round_number in range(3000):
            settings = rng.choice([core.CHECK_AI, core.QUICK_AI])
            behaviour = {m: rng.choice(kinds) for m in self.MODELS}
            clock = self.Clock()
            client, calls = self.gemini(clock, behaviour)
            with mock.patch.object(ex.Extractor, "_client", staticmethod(lambda key: client)):
                extractor = ex.Extractor("key", self.MODELS, "Africa/Cairo", "", **settings)
            with mock.patch.object(ex, "time", clock):
                try:
                    extractor._ask("prompt")
                    answered = True
                except ex.AIUnavailable:
                    answered = False
            detail = (round_number, settings, behaviour, calls)
            self.assertLessEqual(clock.now, settings["give_up_after"] + 1, detail)
            self.assertLessEqual(len(calls), 12, detail)
            # The last model answering at once is always reachable: time is kept for it.
            if behaviour["m5"] == "ok":
                self.assertTrue(answered, detail)
            # Nothing that hangs or is merely busy stands in the way of a model that works.
            if all(how in ("ok", "quota", "minute", "503", "500", "404") for how in behaviour.values()) \
                    and "ok" in behaviour.values():
                self.assertTrue(answered, detail)
                self.assertLess(clock.now, 10, detail)


if __name__ == "__main__":
    unittest.main()


class StateSize(unittest.TestCase):
    """Google cuts a description off at 8,192 characters and still says "saved"."""

    def fullest_state(self, id_digits):
        return {
            "chat_id": -1001234567890123,
            "done": [core.short_id(f"announcement-{10 ** (id_digits - 1) + n}") for n in range(core.REMEMBERED_IDS)],
            "tg_seen": [987654321000 + n for n in range(core.REMEMBERED_UPDATES)],
            "tg_open": 987654321000,
            "notes": {"987654321000": {"text": "x" * (core.NOTES_ROOM - 60), "date": 1794380400}},
            "ai_out": {f"gemini-9.9-flash-lite-preview-{n}": 1794380400 for n in range(5)},
            "ai_slow": {f"gemini-9.9-flash-lite-preview-{n}": 1794380400 for n in range(5)},
            "alerts": {"canvas": "2026-11-12", "ai": "2026-11-12", "calendar": "2026-11-12"},
            "tries": {f"a1234567{n}": 2 for n in range(5)},
            "token": {"created": "2026-11-11", "sent": "2026-11-12", "done": True},
            "token_seen": {"mark": "0123456789", "day": "2026-11-11", "old": "2026-11-11"},
            "digest": "2026-11-12",
            "checking": 1794380400,
        }

    def test_the_fullest_state_fits(self):
        for digits in (5, 8, 9, 15, 20):
            size = len(json.dumps(self.fullest_state(digits), separators=(",", ":")))
            self.assertLess(size, core.STATE_ROOM, digits)
        self.assertLess(core.STATE_ROOM, gcal.STATE_LIMIT)

    def test_long_canvas_numbers_stay_distinct(self):
        ids = {core.short_id(f"announcement-{10 ** 14 + n}") for n in range(5000)}
        self.assertEqual(len(ids), 5000)
        self.assertEqual(core.short_id("announcement-12099"), "a12099")  # short ones are unchanged
        self.assertNotEqual(core.short_id("announcement-123456789"), core.short_id("message-123456789"))

    def test_too_much_is_trimmed_not_cut_off(self):
        with world({"chat_id": OWNER}) as google:
            a = core.Assistant()
            a.state["done"] = [f"a{n:08d}" for n in range(900)]
            a.save()
            stored = google.state()
            self.assertEqual(stored["chat_id"], OWNER)
            self.assertLess(len(stored["done"]), 900)
            self.assertEqual(stored["done"][-1], "a00000899")  # the newest are kept

    def test_a_state_too_long_is_never_sent(self):
        with world({"chat_id": OWNER}) as google:
            calendar = core.Assistant().calendar
            with self.assertRaises(gcal.CalendarProblem):
                calendar.save_state({"chat_id": OWNER, "pad": "x" * 9000})
            self.assertEqual(google.state(), {"chat_id": OWNER})
