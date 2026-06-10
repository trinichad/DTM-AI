"""Scheduler tests — spec parsing + the recurrence tick (due → ready → dispatch, advance next_run)."""
import datetime
import tempfile
import time
import unittest
from pathlib import Path

from execution.core.scheduler import Scheduler, compute_next_run, valid_spec
from execution.core.tasks import TaskStore


def _now_ms() -> int:
    return int(time.time() * 1000)


class FakeDispatcher:
    def __init__(self): self.calls = 0
    def dispatch(self, max_n: int = 8): self.calls += 1; return {"ok": True}


class SpecParsing(unittest.TestCase):
    def test_interval(self):
        now = 1_000_000_000_000
        self.assertEqual(compute_next_run("every 5m", now), now + 5 * 60_000)
        self.assertEqual(compute_next_run("every 2h", now), now + 2 * 3_600_000)
        self.assertEqual(compute_next_run("every 1d", now), now + 86_400_000)
        self.assertEqual(compute_next_run("hourly", now), now + 3_600_000)

    def test_daily_time_is_in_future_at_that_hour(self):
        now = _now_ms()
        nxt = compute_next_run("daily 07:00", now)
        self.assertIsNotNone(nxt)
        self.assertGreater(nxt, now)
        dt = datetime.datetime.fromtimestamp(nxt / 1000.0)
        self.assertEqual((dt.hour, dt.minute), (7, 0))

    def test_weekdays_lands_on_a_weekday(self):
        now = _now_ms()
        nxt = compute_next_run("weekdays 09:30", now)
        self.assertLess(datetime.datetime.fromtimestamp(nxt / 1000.0).weekday(), 5)

    def test_bare_time_and_invalid(self):
        self.assertIsNotNone(compute_next_run("07:00", _now_ms()))
        self.assertIsNone(compute_next_run("whenever i feel like it", _now_ms()))
        self.assertIsNone(compute_next_run("daily 99:99", _now_ms()))
        self.assertFalse(valid_spec("nonsense"))
        self.assertTrue(valid_spec("every 10m"))


class Tick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def test_due_task_fires_and_advances(self):
        past = _now_ms() - 5000
        t = self.store.create("drift check", assignee="patchwright", recurring=True,
                              schedule_spec="every 1h", next_run_at=past)
        self.assertEqual(t["status"], "scheduled")
        disp = FakeDispatcher()
        sched = Scheduler(self.store, disp, tick_seconds=999)
        self.assertEqual(sched.tick(), 1)
        self.assertEqual(disp.calls, 1)                       # dispatch was kicked
        # task is now ready (claimable) and next_run is in the future
        claimed = self.store.claim_next_ready()
        self.assertEqual(claimed["id"], t["id"])
        self.assertTrue(claimed["recurring"])
        row = self.store.get(t["id"])
        self.assertGreater(row["next_run_ms"], _now_ms())

    def test_recurring_completion_returns_to_scheduled(self):
        t = self.store.create("x", assignee="pw", recurring=True, schedule_spec="every 1h",
                              next_run_at=_now_ms() - 1000)
        Scheduler(self.store, FakeDispatcher(), 999).tick()
        self.store.claim_next_ready()                          # → running
        self.store.complete_recurring(t["id"], "all clear")
        self.assertEqual(self.store.get(t["id"])["status"], "scheduled")

    def test_paused_task_does_not_fire(self):
        t = self.store.create("x", assignee="pw", recurring=True, schedule_spec="every 1h",
                              next_run_at=_now_ms() - 1000)
        self.store.set_paused(t["id"], True)
        disp = FakeDispatcher()
        self.assertEqual(Scheduler(self.store, disp, 999).tick(), 0)
        self.assertEqual(disp.calls, 0)

    def test_bad_spec_gets_paused_not_spun(self):
        t = self.store.create("x", assignee="pw", recurring=True, schedule_spec="garbage",
                              next_run_at=_now_ms() - 1000)
        self.assertEqual(Scheduler(self.store, FakeDispatcher(), 999).tick(), 0)
        self.assertTrue(self.store.get(t["id"])["paused"])

    def test_run_now_makes_it_ready(self):
        t = self.store.create("x", assignee="pw", recurring=True, schedule_spec="daily 07:00",
                              next_run_at=_now_ms() + 86_400_000)
        self.store.run_now(t["id"])
        self.assertEqual(self.store.claim_next_ready()["id"], t["id"])


if __name__ == "__main__":
    unittest.main()
