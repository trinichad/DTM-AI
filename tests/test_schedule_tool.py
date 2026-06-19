"""schedule_task tool — creates a recurring board job, validates inputs, and ships a gated posture
(write + approval + disabled-by-default) so the lead can't silently schedule autonomous work."""
import os
import tempfile
import unittest
from pathlib import Path

from execution.core.context import ToolContext
from execution.core.registry import Registry
from execution.core.tasks import TaskStore
from execution.skills import schedule_task


def _write(p: Path, t: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(t)


class ScheduleTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        _write(d / "SOUL.md", "# AtlasOps\n- name: AtlasOps\n")
        _write(d / "profiles" / "patchwright" / "SOUL.md", "# PW\n- name: Patchwright\n")
        self._prev = os.environ.get("MSPAI_AGENTS_DIR")
        os.environ["MSPAI_AGENTS_DIR"] = str(d)
        self.store = TaskStore(d / "t.db")
        self.ctx = ToolContext(tenant_id="acme", actor="alex@mspai", _meta={"tasks": self.store})

    def tearDown(self):
        self.store.close()
        if self._prev is None:
            os.environ.pop("MSPAI_AGENTS_DIR", None)
        else:
            os.environ["MSPAI_AGENTS_DIR"] = self._prev
        self.tmp.cleanup()

    def test_creates_recurring_scheduled_task(self):
        r = schedule_task.run(self.ctx, title="Acme Cylance check", assignee="patchwright",
                              schedule="daily 07:00", instructions="check versions, flag drift")
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "scheduled")
        self.assertGreater(r["next_run_ms"], 0)
        # it really landed on the board, recurring + assigned + bound to the tenant
        t = self.store.get(r["task_id"])
        self.assertTrue(t["recurring"])
        self.assertEqual(t["assignee"], "patchwright")
        self.assertEqual(t["tenant"], "acme")
        self.assertEqual(t["schedule_spec"], "daily 07:00")

    def test_bad_schedule_rejected(self):
        r = schedule_task.run(self.ctx, title="x", assignee="patchwright", schedule="sometimes")
        self.assertFalse(r["ok"])
        self.assertIn("unrecognised", r["error"])

    def test_unknown_agent_rejected(self):
        r = schedule_task.run(self.ctx, title="x", assignee="ghostagent", schedule="hourly")
        self.assertFalse(r["ok"])
        self.assertIn("no specialist", r["error"])

    def test_missing_title_rejected(self):
        r = schedule_task.run(self.ctx, title="  ", assignee="patchwright", schedule="hourly")
        self.assertFalse(r["ok"])

    def test_gated_posture(self):
        """The registry must coerce the tool to write + approval + disabled-by-default."""
        info = Registry().get("schedule_task")
        self.assertIsNotNone(info)
        self.assertEqual(info.category, "write")
        self.assertTrue(info.requires_approval)
        self.assertFalse(info.enabled_by_default)
        self.assertTrue(info.is_write)


if __name__ == "__main__":
    unittest.main()
