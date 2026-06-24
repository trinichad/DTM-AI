"""Native bulk/list params on the Huntress + Freshdesk per-record skills (D-110-style fan-out).

Each tool now takes a list param (agent_ids/incident_ids/escalation_ids/ticket_ids/contact_ids/
company_ids) alongside the existing single id, so the agent acts on MANY records in ONE call
instead of looping. We assert: a list yields one result row per id (each carrying its own id),
the <thing>_done count is correct, ok_count is right, and the single-id path is unchanged.
Reuses the fakes/ctx from the existing Huntress (test_edr_skills) + Freshdesk suites — no edits
to those files."""
import unittest

from tests.test_edr_skills import FakeEDR, _ctx as _edr_ctx
from tests.test_freshdesk import FakeFD, _ctx as _fd_ctx


class NativeHuntress(unittest.TestCase):
    def test_get_agent_batch_and_single(self):
        from execution.skills import huntress_get_agent as ga
        fake = FakeEDR(get_data={"id": 1, "os": "Windows"})
        r = ga.run(_edr_ctx(fake), agent_ids=["11", "22"])
        self.assertEqual(r["agents_done"], 2)
        self.assertEqual(len(r["results"]), 2)
        # both ids were fetched
        self.assertEqual([p for p, _ in fake.gets], ["/agents/11", "/agents/22"])
        # bad id is rejected per-row, still attributable
        bad = ga.run(_edr_ctx(FakeEDR()), agent_ids=["nope"])
        self.assertEqual(bad["agents_done"], 1)
        self.assertEqual(bad["ok_count"], 0)
        self.assertFalse(bad["results"][0]["ok"])
        self.assertEqual(bad["results"][0]["agent_id"], "nope")
        # single path unchanged: a bare passthrough dict, not a {results:[...]} envelope
        f2 = FakeEDR(get_data={"id": 1, "os": "Windows"})
        one = ga.run(_edr_ctx(f2), agent_id="11")
        self.assertNotIn("results", one)
        self.assertEqual(f2.gets[0][0], "/agents/11")

    def test_resolve_incident_batch_and_single(self):
        from execution.skills import huntress_resolve_incident as ri
        fake = FakeEDR()
        r = ri.run(_edr_ctx(fake), incident_ids=["7", "8"], note="benign")
        self.assertEqual(r["incidents_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertTrue(r["ok"])
        # one resolution POST per incident, each carrying the shared note
        self.assertEqual([p for _, p, _ in fake.writes],
                         ["/incident_reports/7/resolution", "/incident_reports/8/resolution"])
        for _, _, body in fake.writes:
            self.assertEqual(body, {"note": "benign"})
        # per-row attribution
        self.assertEqual({row["incident_id"] for row in r["results"]}, {"7", "8"})
        # bad id rejected per-row, nothing written for it
        f2 = FakeEDR()
        mixed = ri.run(_edr_ctx(f2), incident_ids=["7", "bad"])
        self.assertEqual(mixed["incidents_done"], 2)
        self.assertEqual(mixed["ok_count"], 1)
        self.assertTrue(mixed["ok"])                     # any() — one succeeded
        self.assertEqual(mixed["results"][1]["incident_id"], "bad")
        self.assertEqual(len(f2.writes), 1)              # only the valid one hit the API
        # single path unchanged
        f3 = FakeEDR()
        one = ri.run(_edr_ctx(f3), incident_id="7", note="benign")
        self.assertTrue(one["ok"])
        self.assertNotIn("results", one)
        self.assertEqual(f3.writes[0], ("POST", "/incident_reports/7/resolution", {"note": "benign"}))


class NativeFreshdesk(unittest.TestCase):
    def test_get_ticket_batch_and_single(self):
        from execution.skills import freshdesk_get_ticket as gt
        fake = FakeFD(get_data={"id": 5, "subject": "S"})
        r = gt.run(_fd_ctx(fake), ticket_ids=[5, 6], include="stats")
        self.assertEqual(r["tickets_done"], 2)
        self.assertEqual(len(r["results"]), 2)
        # both ids fetched with the shared include param threaded through
        self.assertEqual([p for p, _ in fake.gets], ["/tickets/5", "/tickets/6"])
        for _, params in fake.gets:
            self.assertEqual(params, {"include": "stats"})
        # single path unchanged: raw passthrough, not an envelope
        f2 = FakeFD(get_data={"id": 5})
        one = gt.run(_fd_ctx(f2), ticket_id=5)
        self.assertNotIn("results", one)
        self.assertEqual(f2.gets[0], ("/tickets/5", None))

    def test_update_ticket_batch_and_single(self):
        from execution.skills import freshdesk_update_ticket as ut
        fake = FakeFD()
        r = ut.run(_fd_ctx(fake), ticket_ids=[9, 10], status="resolved")
        self.assertEqual(r["tickets_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertTrue(r["ok"])
        # same change (status resolved -> 4) applied to each ticket
        self.assertEqual(fake.writes,
                         [("PUT", "/tickets/9", {"status": 4}),
                          ("PUT", "/tickets/10", {"status": 4})])
        self.assertEqual({row["ticket_id"] for row in r["results"]}, {9, 10})
        # no-field batch: each row fails per-row, nothing written
        f2 = FakeFD()
        empty = ut.run(_fd_ctx(f2), ticket_ids=[9, 10])
        self.assertEqual(empty["ok_count"], 0)
        self.assertFalse(empty["ok"])
        self.assertEqual(f2.writes, [])
        # single path unchanged
        f3 = FakeFD()
        ut.run(_fd_ctx(f3), ticket_id=9, status="resolved")
        self.assertEqual(f3.writes[0], ("PUT", "/tickets/9", {"status": 4}))
        self.assertFalse(ut.run(_fd_ctx(FakeFD()), ticket_id=9)["ok"])   # nothing to change

    def test_delete_ticket_batch_is_destructive_path(self):
        from execution.skills import freshdesk_delete_ticket as dt
        fake = FakeFD()
        r = dt.run(_fd_ctx(fake), ticket_ids=[8, 12])
        self.assertEqual(r["tickets_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        # one destructive DELETE per ticket
        self.assertEqual(fake.writes,
                         [("DELETE", "/tickets/8", None), ("DELETE", "/tickets/12", None)])
        self.assertEqual({row["ticket_id"] for row in r["results"]}, {8, 12})
        # single path unchanged
        f2 = FakeFD()
        one = dt.run(_fd_ctx(f2), ticket_id=8)
        self.assertNotIn("results", one)
        self.assertEqual(f2.writes[0], ("DELETE", "/tickets/8", None))


if __name__ == "__main__":
    unittest.main()
