"""Teams bridge interactions (D-88): slash commands, profile switch, approve/repeat/deny."""
import unittest

import execution.core.dispatch as dispatch_mod
from execution.core.teams_bot import TeamsBridge


class _Approvals:
    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.rejected, self.claimed, self.results = [], [], []

    def get(self, i):
        return dict(self.rows[i]) if i in self.rows else None

    def list(self, status=None):
        return [dict(r) for r in self.rows.values() if (status is None or r["status"] == status)]

    def reject(self, i, by):
        self.rows[i]["status"] = "rejected"; self.rejected.append((i, by)); return True

    def claim_for_execution(self, i, by):
        if self.rows[i]["status"] != "pending":
            return False
        self.claimed.append((i, by)); return True

    def mark_result(self, i, ok):
        self.rows[i]["status"] = "executed" if ok else "failed"; self.results.append((i, ok))


class _Gate:
    def __init__(self):
        self.batches = []

    def grant_batch(self, tenant, tool, *, approval_id=None, by=None, count=None):
        self.batches.append((tenant, tool, approval_id)); return {"granted": 10}


class _Audit:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)


class _Agent:
    def __init__(self, rows):
        self.approvals = _Approvals(rows)
        self.gate = _Gate()
        self.audit = _Audit()
        self.registry = object()


_ADMIN_ENV = {"TEAMS_ALLOWED_USERS": "AAD-ADMIN|Alex|alex", "TEAMS_BIND_TENANT": "acme"}


def _bridge(rows):
    return TeamsBridge(_Agent(rows),
                       user_lookup=lambda u: {"email": "c@x", "role": "admin"} if u == "alex" else None)


def _pending(i=7, tool="kaseya_run_command", cat="write"):
    return {"id": i, "actor": "teams:Alex (AAD-ADMIN)", "tenant_id": "acme", "tool": tool,
            "category": cat, "args": {"machine": "pc1", "command": "whoami"}, "status": "pending"}


class Commands(unittest.TestCase):
    def test_help_and_unknown(self):
        b = _bridge([])
        self.assertIn("/agents", b._command(_ADMIN_ENV, "AAD-ADMIN", "Alex", "c1", "", "/help"))
        self.assertIn("Unknown command", b._command(_ADMIN_ENV, "AAD-ADMIN", "Alex", "c1", "", "/zzz"))

    def test_list_and_switch_profile(self):
        b = _bridge([])
        # default active profile
        self.assertEqual(b._active_profile("c1", _ADMIN_ENV), "default")
        listing = b._command(_ADMIN_ENV, "AAD-ADMIN", "Alex", "c1", "", "/agents")
        self.assertIn("default", listing)
        # switching to an unknown agent is rejected
        self.assertIn("No agent", b._command(_ADMIN_ENV, "AAD-ADMIN", "Alex", "c1", "", "/agent nope"))
        self.assertEqual(b._active_profile("c1", _ADMIN_ENV), "default")   # unchanged

    def test_whoami_shows_link_and_scope(self):
        b = _bridge([])
        out = b._command(_ADMIN_ENV, "AAD-ADMIN", "Alex", "c1", "", "/whoami")
        self.assertIn("alex", out)
        self.assertIn("admin", out)
        self.assertIn("acme", out)


class Approvals(unittest.TestCase):
    def setUp(self):
        self._orig = dispatch_mod.dispatch
        dispatch_mod.dispatch = lambda **kw: {"ok": True, "source": "x", "data": {}, "error": None}

    def tearDown(self):
        dispatch_mod.dispatch = self._orig

    def test_non_admin_cannot_decide(self):
        b = _bridge([_pending()])
        # an unlinked Teams user (no allowlist link → role "") is refused
        d = b._decide({"TEAMS_ALLOWED_USERS": "OTHER|Bob"}, "OTHER", "Bob", "c1", "approve", 7, False)
        self.assertIn("admin-linked", d["reply"])
        self.assertIsNone(d["continue"])
        self.assertEqual(b.agent.approvals.get(7)["status"], "pending")    # nothing ran

    def test_approve_executes_and_signals_continue(self):
        b = _bridge([_pending()])
        d = b._decide(_ADMIN_ENV, "AAD-ADMIN", "Alex", "c1", "approve", 7, False)
        self.assertIn("Approved & ran", d["reply"])
        self.assertIsNotNone(d["continue"])                # → caller runs the follow-up turn
        self.assertEqual(b.agent.approvals.get(7)["status"], "executed")
        self.assertEqual(b.agent.approvals.claimed, [(7, "alex")])

    def test_approve_repeat_arms_batch(self):
        b = _bridge([_pending()])
        d = b._decide(_ADMIN_ENV, "AAD-ADMIN", "Alex", "c1", "approve", 7, True)
        self.assertIn("Auto-approving", d["reply"])
        self.assertEqual(b.agent.gate.batches, [("acme", "kaseya_run_command", 7)])

    def test_deny(self):
        b = _bridge([_pending()])
        d = b._decide(_ADMIN_ENV, "AAD-ADMIN", "Alex", "c1", "deny", 7, False)
        self.assertIn("Denied", d["reply"])
        self.assertIsNone(d["continue"])
        self.assertEqual(b.agent.approvals.get(7)["status"], "rejected")

    def test_latest_pending_when_no_id(self):
        b = _bridge([_pending(7), _pending(9)])
        d = b._decide(_ADMIN_ENV, "AAD-ADMIN", "Alex", "c1", "approve", None, False)
        self.assertIn("#9", d["reply"])                    # newest pending picked
        self.assertEqual(b.agent.approvals.get(9)["status"], "executed")

    def test_parse_decision(self):
        self.assertEqual(TeamsBridge._parse_decision("/approve 7"), ("approve", 7, False))
        self.assertEqual(TeamsBridge._parse_decision("/repeat #9"), ("approve", 9, True))
        self.assertEqual(TeamsBridge._parse_decision("/deny"), ("deny", None, False))
        self.assertIsNone(TeamsBridge._parse_decision("/agents"))

    def test_card_action_shape(self):
        card = TeamsBridge._approval_card(_pending(7))
        self.assertEqual(card["type"], "AdaptiveCard")
        actions = {a["title"]: a["data"] for a in card["actions"]}
        self.assertEqual(actions["✅ Approve"], {"mspai_action": "approve", "approval_id": 7})
        self.assertTrue(actions["🔁 Approve + repeat"]["batch"])
        self.assertEqual(actions["⛔ Deny"]["mspai_action"], "deny")


if __name__ == "__main__":
    unittest.main()
