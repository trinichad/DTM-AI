"""Freshdesk skills (D-83) — validation + correct path/body mapping via a fake client."""
import unittest

from execution.core.context import ToolContext


class FakeFD:
    def __init__(self, get_data=None, rows=None):
        self.get_data = get_data if get_data is not None else {}
        self.rows = rows or []
        self.gets, self.writes = [], []

    def get(self, path, params=None):
        self.gets.append((path, params))
        return self.get_data

    def get_paginated(self, path, params=None, **_):
        self.gets.append((path, params))
        yield from self.rows

    def write(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"id": 101, **(body or {})}

    def write_destructive(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"ok": True}


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda integ, tenant: fake)


class FreshdeskTickets(unittest.TestCase):
    def test_create_ticket_maps_words_to_ids(self):
        from execution.skills import freshdesk_create_ticket as ct
        fake = FakeFD()
        r = ct.run(_ctx(fake), subject="Printer down", description="It won't print",
                   email="user@demodomain.com", priority="high", status="open", agent_id=5)
        self.assertTrue(r["ok"], r)
        m, p, body = fake.writes[0]
        self.assertEqual((m, p), ("POST", "/tickets"))
        self.assertEqual(body["priority"], 3)          # high -> 3
        self.assertEqual(body["status"], 2)            # open -> 2
        self.assertEqual(body["email"], "user@demodomain.com")
        self.assertEqual(body["responder_id"], 5)

    def test_create_ticket_needs_requester(self):
        from execution.skills import freshdesk_create_ticket as ct
        fake = FakeFD()
        r = ct.run(_ctx(fake), subject="x", description="y")     # no email/requester
        self.assertFalse(r["ok"])
        self.assertEqual(fake.writes, [])

    def test_update_ticket_partial(self):
        from execution.skills import freshdesk_update_ticket as ut
        fake = FakeFD()
        ut.run(_ctx(fake), ticket_id=9, status="resolved")
        self.assertEqual(fake.writes[0], ("PUT", "/tickets/9", {"status": 4}))
        self.assertFalse(ut.run(_ctx(FakeFD()), ticket_id=9)["ok"])   # nothing to change

    def test_reply_vs_note(self):
        from execution.skills import freshdesk_reply_ticket as rp, freshdesk_add_note as an
        fr = FakeFD()
        rp.run(_ctx(fr), ticket_id=3, body="Thanks, fixed.")
        self.assertEqual(fr.writes[0][:2], ("POST", "/tickets/3/reply"))
        fn = FakeFD()
        an.run(_ctx(fn), ticket_id=3, body="internal")
        self.assertEqual(fn.writes[0][1], "/tickets/3/notes")
        self.assertTrue(fn.writes[0][2]["private"])        # private by default

    def test_delete_is_destructive_path(self):
        from execution.skills import freshdesk_delete_ticket as dt
        fake = FakeFD()
        dt.run(_ctx(fake), ticket_id=8)
        self.assertEqual(fake.writes[0], ("DELETE", "/tickets/8", None))

    def test_merge_excludes_primary(self):
        from execution.skills import freshdesk_merge_tickets as mt
        fake = FakeFD()
        mt.run(_ctx(fake), primary_id=1, ticket_ids=[1, 2, 3])
        self.assertEqual(fake.writes[0][2]["ticket_ids"], [2, 3])      # primary filtered out

    def test_list_tickets_slims_to_words(self):
        from execution.skills import freshdesk_list_tickets as lt
        fake = FakeFD(rows=[{"id": 1, "subject": "S", "status": 3, "priority": 4}])
        out = lt.run(_ctx(fake), status="pending")
        self.assertEqual(out[0]["status"], "Pending")
        self.assertEqual(out[0]["priority"], "Urgent")
        self.assertEqual(fake.gets[0][1]["status"], 3)                  # filter mapped to id


class FreshdeskOther(unittest.TestCase):
    def test_create_contact_requires_a_channel(self):
        from execution.skills import freshdesk_create_contact as cc
        self.assertFalse(cc.run(_ctx(FakeFD()), name="Bob")["ok"])     # no email/phone/mobile
        fake = FakeFD()
        r = cc.run(_ctx(fake), name="Bob", email="bob@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0][:2], ("POST", "/contacts"))

    def test_time_entry_format(self):
        from execution.skills import freshdesk_create_time_entry as ce
        self.assertFalse(ce.run(_ctx(FakeFD()), ticket_id=1, time_spent="90")["ok"])  # not HH:MM
        fake = FakeFD()
        ce.run(_ctx(fake), ticket_id=1, time_spent="01:30")
        self.assertEqual(fake.writes[0], ("POST", "/tickets/1/time_entries",
                                          {"time_spent": "01:30", "billable": True}))

    def test_make_agent_path(self):
        from execution.skills import freshdesk_make_agent as ma
        fake = FakeFD()
        ma.run(_ctx(fake), contact_id=12)
        self.assertEqual(fake.writes[0], ("PUT", "/contacts/12/make_agent", None))

    def test_create_article_status_default_draft(self):
        from execution.skills import freshdesk_create_solution_article as ca
        fake = FakeFD()
        ca.run(_ctx(fake), folder_id=4, title="How to VPN", description="<p>steps</p>")
        m, p, body = fake.writes[0]
        self.assertEqual((m, p), ("POST", "/solutions/folders/4/articles"))
        self.assertEqual(body["status"], 1)            # draft

    def test_groups_registered(self):
        from execution.core.tool_groups import GROUP_INFO
        for g in ("freshdesk_tickets", "freshdesk_contacts", "freshdesk_team", "freshdesk_time",
                  "freshdesk_kb", "freshdesk_admin"):
            self.assertIn(g, GROUP_INFO)
            self.assertTrue(GROUP_INFO[g]["setup"])


if __name__ == "__main__":
    unittest.main()
