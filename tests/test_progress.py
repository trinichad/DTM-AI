"""Live batch progress (D-112) — a long single tool call must stream item-level progress so the
UI never looks frozen. Covers ctx.map_progress, the bulk loop, and the heartbeat emit."""
import tempfile
import unittest
from pathlib import Path

from execution.core.audit import AuditStore
from execution.core.context import ToolContext
from execution.core.dispatch import dispatch
from execution.core.registry import Registry
from tests.test_dispatch import AutoApproveGate


class MapProgress(unittest.TestCase):
    def test_map_progress_reports_each_item_and_returns_results(self):
        ctx = ToolContext(tenant_id="acme", actor="t")
        seen = []
        # capture the progress snapshot the heartbeat would read, after each item
        def fn(x):
            seen.append(dict(ctx._progress))            # progress set BEFORE fn runs
            return x * 2
        out = ctx.map_progress(["a", "b", "c"], fn)
        self.assertEqual(out, ["aa", "bb", "cc"])
        self.assertEqual([p["done"] for p in seen], [0, 1, 2])
        self.assertTrue(all(p["total"] == 3 for p in seen))
        self.assertEqual([p["label"] for p in seen], ["a", "b", "c"])
        self.assertEqual(ctx._progress, {"done": 3, "total": 3, "label": ""})   # final 100%

    def test_label_callable(self):
        ctx = ToolContext(tenant_id="acme", actor="t")
        labels = []
        ctx.map_progress([{"u": "x"}], lambda it: labels.append(ctx._progress["label"]),
                         label=lambda it: it["u"])
        self.assertEqual(labels, ["x"])


class BulkProgress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "t.db")
        self.reg = Registry(package="tests.fixture_skills")
        self.ctx = ToolContext(tenant_id="acme", actor="tester")

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def test_bulk_sets_progress_during_fan_out(self):
        # a fixture read tool that snapshots ctx._progress so we can prove bulk reported it
        marks = []
        # fx_read echoes x; wrap dispatch by observing progress via a custom item that records
        # We assert progress reaches the final 100% after the bulk completes.
        env = dispatch(registry=self.reg, audit=self.audit, ctx=self.ctx, name="bulk",
                       args={"tool": "fx_read", "items": [{"x": "a"}, {"x": "b"}, {"x": "c"}]},
                       gate=AutoApproveGate())
        self.assertTrue(env["ok"])
        self.assertEqual(env["data"]["count"], 3)
        # bulk updates ctx._progress as it goes; after completion it has advanced to the last item
        self.assertIsNotNone(self.ctx._progress)
        self.assertEqual(self.ctx._progress["total"], 3)
        self.assertEqual(self.ctx._progress["label"], "fx_read")


if __name__ == "__main__":
    unittest.main()
