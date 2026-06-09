"""tasks tests — native delegation board (TaskStore) + the Dispatcher that runs the agent loop."""
import tempfile
import time
import types
import unittest
from pathlib import Path

from execution.core.tasks import Dispatcher, TaskStore


class FakeAgent:
    """Stand-in for Agent: records chat() calls; returns a turn-like object or raises."""
    cfg = None

    def __init__(self, answer="all clear", fail=False):
        self.answer = answer
        self.fail = fail
        self.calls = []

    def chat(self, ctx, message, *, profile=None, **kw):
        self.calls.append({"message": message, "profile": profile, "ctx": ctx})
        if self.fail:
            raise RuntimeError("provider unreachable")
        return types.SimpleNamespace(answer=self.answer, citations=[], tool_events=[],
                                     provider="mock", model="m", rounds=1)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_create_unassigned_is_triage(self):
        t = self.store.create("check mfa gaps")
        self.assertEqual(t["status"], "triage")
        self.assertEqual(t["assignee"], "")
        self.assertTrue(t["id"].startswith("t_"))

    def test_create_assigned_is_ready(self):
        t = self.store.create("audit acme", assignee="sentinelops", tenant="acme")
        self.assertEqual(t["status"], "ready")
        self.assertEqual(t["assignee"], "sentinelops")
        self.assertEqual(t["tenant"], "acme")

    def test_create_requires_title(self):
        with self.assertRaises(ValueError):
            self.store.create("   ")

    def test_create_bad_assignee_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create("x", assignee="../evil")

    def test_idempotency_key_dedups(self):
        a = self.store.create("once", assignee="sentinelops", idempotency_key="k1")
        b = self.store.create("once", assignee="sentinelops", idempotency_key="k1")
        self.assertEqual(a["id"], b["id"])

    def test_board_groups_and_counts(self):
        self.store.create("a", assignee="sentinelops")     # ready
        self.store.create("b")                              # triage
        b = self.store.board()
        self.assertTrue(b["available"])
        self.assertEqual(b["counts"]["ready"], 1)
        self.assertEqual(b["counts"]["triage"], 1)
        self.assertEqual(b["total"], 2)
        self.assertEqual(b["by_assignee"]["sentinelops"], 1)
        cols = {c["status"]: c["tasks"] for c in b["columns"]}
        self.assertEqual(len(cols["ready"]), 1)

    def test_assign_moves_triage_to_ready(self):
        t = self.store.create("b")
        r = self.store.assign(t["id"], "patchwright")
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["assignee"], "patchwright")

    def test_unassign_sends_ready_to_triage(self):
        t = self.store.create("b", assignee="patchwright")
        r = self.store.assign(t["id"], "none")
        self.assertEqual(r["assignee"], "")
        self.assertEqual(r["status"], "triage")

    def test_archive_hides_from_board_but_keeps_record(self):
        t = self.store.create("b", assignee="x")
        self.store.archive(t["id"])
        self.assertEqual(self.store.board()["total"], 0)
        self.assertEqual(self.store.get(t["id"])["status"], "archived")

    def test_archive_unknown_rejected(self):
        with self.assertRaises(ValueError):
            self.store.archive("t_nope")

    def test_get_detail_shape(self):
        t = self.store.create("b", assignee="x", body="do the thing")
        full = self.store.get(t["id"])
        self.assertEqual(full["body"], "do the thing")
        self.assertEqual(full["workspace_kind"], "native")
        self.assertEqual(full["comments"], [])
        self.assertIsInstance(full["runs"], list)
        self.assertTrue(any(e["kind"] == "created" for e in full["events"]))

    def test_claim_is_atomic(self):
        t = self.store.create("b", assignee="x")           # ready
        first = self.store.claim_next_ready()
        self.assertEqual(first["id"], t["id"])
        self.assertIsNone(self.store.claim_next_ready())    # nothing left ready
        self.assertEqual(self.store.get(t["id"])["status"], "running")


class DispatcherRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _disp(self, agent):
        return Dispatcher(self.store, agent, lambda tenant, actor: ("ctx", tenant, actor))

    def test_run_one_success_moves_to_review(self):
        agent = FakeAgent(answer="acme has 3 mfa gaps")
        t = self.store.create("mfa?", assignee="sentinelops", body="acme", tenant="acme")
        claimed = self.store.claim_next_ready()
        self._disp(agent)._run_one(claimed)
        # ran AS the profile, with title+body in the message, bound to the task's tenant
        self.assertEqual(agent.calls[0]["profile"], "sentinelops")
        self.assertIn("mfa?", agent.calls[0]["message"])
        self.assertIn("acme", agent.calls[0]["message"])
        self.assertEqual(agent.calls[0]["ctx"][1], "acme")          # ctx_factory got the tenant
        full = self.store.get(t["id"])
        self.assertEqual(full["status"], "review")
        self.assertEqual(full["result"], "acme has 3 mfa gaps")
        self.assertEqual(full["runs"][-1]["outcome"], "ok")
        card = next(c for col in self.store.board()["columns"] for c in col["tasks"]
                    if c["id"] == t["id"])
        self.assertEqual(card["latest_summary"], "acme has 3 mfa gaps")   # board shows the outcome

    def test_run_one_failure_blocks(self):
        agent = FakeAgent(fail=True)
        t = self.store.create("boom", assignee="x")
        claimed = self.store.claim_next_ready()
        self._disp(agent)._run_one(claimed)
        full = self.store.get(t["id"])
        self.assertEqual(full["status"], "blocked")
        self.assertEqual(full["consecutive_failures"], 1)
        self.assertIn("unreachable", full["last_failure_error"])
        self.assertEqual(full["runs"][-1]["outcome"], "fail")

    def test_dispatch_claims_and_runs_all(self):
        agent = FakeAgent(answer="done")
        for i in range(3):
            self.store.create(f"task{i}", assignee="x")
        r = self._disp(agent).dispatch(max_n=8)
        self.assertEqual(r["spawned"], 3)
        for _ in range(100):                                # daemon threads finish near-instantly
            if self.store.board()["counts"]["review"] == 3:
                break
            time.sleep(0.02)
        self.assertEqual(self.store.board()["counts"]["review"], 3)
        self.assertEqual(self.store.board()["counts"]["ready"], 0)


if __name__ == "__main__":
    unittest.main()
