"""Clock and timers: parsing a time, setting, listing, clearing and waiting, with a fixed 'now'. No GitHub, no Godot."""
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path
from skyrunner_mcp import clock

NOW = datetime(2026, 10, 12, 15, 58, 0, tzinfo=timezone.utc).timestamp()


class Parsing(unittest.TestCase):
    def test_a_time_of_day_is_the_next_time_the_clock_reads_it(self):
        later = clock.parse_when("16:11", 0, "UTC", NOW)
        self.assertEqual(later - NOW, 13 * 60)
        tomorrow = clock.parse_when("15:00", 0, "UTC", NOW)
        self.assertEqual(tomorrow - NOW, 23 * 3600 + 2 * 60, "a time already past today means tomorrow")

    def test_twelve_hour_times_and_iso_dates(self):
        self.assertEqual(clock.parse_when("4:11 PM", 0, "UTC", NOW), clock.parse_when("16:11", 0, "UTC", NOW))
        self.assertEqual(clock.parse_when("12:05 am", 0, "UTC", NOW), clock.parse_when("00:05", 0, "UTC", NOW))
        self.assertEqual(clock.parse_when("2026-10-13T09:00", 0, "UTC", NOW) - NOW, 17 * 3600 + 2 * 60)

    def test_minutes_from_now_and_bad_input(self):
        self.assertEqual(clock.parse_when("", 30, "UTC", NOW), NOW + 1800)
        for bad in (("", 0), ("16:11", 5), ("25:00", 0), ("soon", 0), ("13:00 pm", 0)):
            with self.assertRaises(ValueError, msg=str(bad)):
                clock.parse_when(bad[0], bad[1], "UTC", NOW)
        with self.assertRaises(ValueError):
            clock.parse_when("", 60 * 24 * 15, "UTC", NOW)
        with self.assertRaisesRegex(ValueError, "Unknown timezone"):
            clock.now("Mars/Olympus")

    def test_now_reads_in_the_zone_asked_for(self):
        sydney = clock.now("Australia/Sydney", NOW)
        self.assertEqual(sydney["time"], "02:58:00")
        self.assertEqual(sydney["date"], "2026-10-13")
        self.assertEqual(clock.now("UTC", NOW)["epoch"], int(NOW))


class Timers(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.clock = clock.Clock(Path(self.dir.name) / "board.sqlite3")

    def tearDown(self):
        self.dir.cleanup()

    def test_a_timer_is_pending_then_due_then_cleared(self):
        tid = self.clock.set("claude", "check the board", NOW + 600, "codex", NOW)
        row = self.clock.rows(at_now=NOW, tz_name="UTC")[0]
        self.assertEqual((row["id"], row["status"], row["seconds_left"], row["agent"]), (tid, "pending", 600, "codex"))
        self.assertEqual(self.clock.rows(at_now=NOW + 601, tz_name="UTC")[0]["status"], "due")
        self.assertEqual(self.clock.remaining(tid, NOW + 100), 500)
        self.assertEqual(self.clock.remaining(tid, NOW + 700), 0)
        self.clock.clear(tid, NOW + 700)
        self.assertEqual(self.clock.rows(at_now=NOW + 700), [], "a cleared timer leaves the list")
        self.assertEqual(self.clock.rows(include_cleared=True, at_now=NOW + 700)[0]["status"], "cleared")
        with self.assertRaises(ValueError):
            self.clock.clear(tid)

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.clock.set("claude", "past", NOW - 1, "", NOW)
        with self.assertRaises(ValueError):
            self.clock.set("claude", "   ", NOW + 60, "", NOW)
        with self.assertRaises(ValueError):
            self.clock.set("nobody", "x", NOW + 60, "", NOW)
        with self.assertRaises(ValueError):
            self.clock.remaining(99)

    def test_timers_sort_by_due_time(self):
        self.clock.set("claude", "second", NOW + 120, "", NOW)
        self.clock.set("codex", "first", NOW + 60, "", NOW)
        self.assertEqual([r["label"] for r in self.clock.rows(at_now=NOW)], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
