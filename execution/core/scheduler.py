"""Scheduler — the recurrence tick behind the Delegation board (SOP: scheduled-delegation.md).

A single daemon thread that, on each tick, finds recurring board tasks whose next_run_at has
passed, flips them to `ready` (advancing next_run_at to the NEXT occurrence up front, so a crash
mid-run never double-fires), then asks the Dispatcher to run them. Stdlib-only.

Schedule specs (server local time):
  every <N>m|h|d   ·   hourly   ·   daily   ·   daily HH:MM   ·   weekdays HH:MM   ·   HH:MM
"""
from __future__ import annotations

import datetime
import re
import threading
import time
from typing import Any, Optional

TICK_SECONDS = 30

_INTERVAL_RE = re.compile(r"^every\s+(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$")
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _unit_ms(u: str) -> int:
    if u.startswith("m"):
        return 60_000
    if u.startswith("h"):
        return 3_600_000
    return 86_400_000


def compute_next_run(spec: str, now_ms: int) -> Optional[int]:
    """Next fire time (epoch ms) for a schedule spec, or None if it can't be parsed."""
    s = (spec or "").strip().lower()
    if not s:
        return None
    m = _INTERVAL_RE.match(s)
    if m:
        return now_ms + int(m.group(1)) * _unit_ms(m.group(2))
    if s == "hourly":
        return now_ms + 3_600_000
    if s == "daily":
        return now_ms + 86_400_000
    weekdays = s.startswith("weekdays")
    tm = _TIME_RE.search(s)
    if tm and (weekdays or s.startswith("daily") or s.startswith("@") or _TIME_RE.fullmatch(s)):
        hh, mm = int(tm.group(1)), int(tm.group(2))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            return None
        base = datetime.datetime.fromtimestamp(now_ms / 1000.0)
        cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand.timestamp() <= now_ms / 1000.0:
            cand += datetime.timedelta(days=1)
        if weekdays:
            while cand.weekday() >= 5:                 # Sat=5, Sun=6 → push to Monday
                cand += datetime.timedelta(days=1)
        return int(cand.timestamp() * 1000)
    return None


def valid_spec(spec: str) -> bool:
    return compute_next_run(spec, _now_ms()) is not None


class Scheduler:
    """Polls the board for due recurring tasks and fires them. Start()ed only by the long-running
    server (never in tests, which call tick() directly)."""

    def __init__(self, store: Any, dispatcher: Any, tick_seconds: int = TICK_SECONDS) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def tick(self) -> int:
        """One pass: enqueue every due recurring task, then dispatch. Returns how many fired."""
        now = _now_ms()
        fired = 0
        for t in self.store.due_recurring(now):
            nxt = compute_next_run(t.get("schedule_spec") or "", now)
            if nxt is None:                            # unparseable spec → pause, don't spin
                self.store.set_paused(t["id"], True)
                continue
            if self.store.enqueue_recurring(t["id"], nxt):
                fired += 1
        if fired:
            self.dispatcher.dispatch()
        return fired

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(self.tick_seconds):
                try:
                    self.tick()
                except Exception:                      # a bad tick must never kill the scheduler
                    pass

        self._thread = threading.Thread(target=loop, name="mspai-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
