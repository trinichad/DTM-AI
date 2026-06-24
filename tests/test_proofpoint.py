"""Proofpoint Essentials connector (D-86) — client write surface + skill path/body via fakes."""
import unittest

from execution.core.context import ToolContext
from execution.clients.proofpoint import ProofpointClient


class ProofpointClientTests(unittest.TestCase):
    def test_base_and_headers(self):
        c = ProofpointClient("us3", "admin@x.com", "secret")
        self.assertEqual(c.base, "https://us3.proofpointessentials.com/api/v1")
        self.assertEqual(c._headers, {"X-User": "admin@x.com", "X-Password": "secret"})
        self.assertEqual(ProofpointClient("https://eu1.proofpointessentials.com/api/v1", "u", "p").base,
                         "https://eu1.proofpointessentials.com/api/v1")
        with self.assertRaises(ValueError):
            ProofpointClient("us1", "", "p")

    def test_write_allowlist_and_destructive(self):
        calls = []

        def t(method, url, headers=None, params=None, json_body=None, verify_tls=True):
            calls.append((method, url.split("/api/v1", 1)[-1]))
            return 200, {"ok": True}
        c = ProofpointClient("us1", "u", "p", transport=t)
        self.assertNotIn("error", c.write("POST", "/orgs/acme.com/users", {"primary_email": "b@acme.com"}))
        self.assertNotIn("error", c.write("PUT", "/orgs/acme.com/users/b@acme.com", {"is_active": False}))
        self.assertNotIn("error", c.write("PUT", "/orgs/acme.com", {"name": "Acme"}))
        self.assertIn("error", c.write("DELETE", "/orgs/acme.com/users/b@acme.com"))   # destructive path
        self.assertIn("error", c.write("POST", "/orgs/acme.com/domains", {}))          # not allow-listed
        self.assertNotIn("error", c.write_destructive("DELETE", "/orgs/acme.com/users/b@acme.com"))
        self.assertEqual(len(calls), 4)


class FakePP:
    def __init__(self, get_data=None, rows=None):
        self.get_data = get_data if get_data is not None else {}
        self.rows = rows if rows is not None else []
        self.gets, self.writes = [], []

    def get(self, path, params=None):
        self.gets.append((path, params))
        return self.get_data if not self.rows else self.rows

    def write(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"ok": True, **(body or {})}

    def write_destructive(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"ok": True}


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda integ, tenant: fake)


class ProofpointSkills(unittest.TestCase):
    def test_create_user_body(self):
        from execution.skills import proofpoint_create_user as cu
        fake = FakePP()
        r = cu.run(_ctx(fake), domain="acme.com", email="bob@acme.com",
                   first_name="Bob", last_name="Lee")
        self.assertTrue(r["ok"], r)
        m, p, body = fake.writes[0]
        self.assertEqual((m, p), ("POST", "/orgs/acme.com/users"))
        self.assertEqual(body["primary_email"], "bob@acme.com")
        self.assertEqual((body["firstname"], body["surname"]), ("Bob", "Lee"))

    def test_update_user_disable(self):
        from execution.skills import proofpoint_update_user as uu
        fake = FakePP()
        uu.run(_ctx(fake), domain="acme.com", email="bob@acme.com", active=False)
        self.assertEqual(fake.writes[0], ("PUT", "/orgs/acme.com/users/bob@acme.com",
                                          {"is_active": False}))
        self.assertFalse(uu.run(_ctx(FakePP()), domain="acme.com", email="bob@acme.com")["ok"])

    def test_allow_sender_get_then_put(self):
        from execution.skills import proofpoint_allow_sender as al
        fake = FakePP(get_data={"primary_email": "bob@acme.com", "safe_sender_list": ["old@x.com"]})
        r = al.run(_ctx(fake), domain="acme.com", email="bob@acme.com", sender="vip@partner.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.gets[0][0], "/orgs/acme.com/users/bob@acme.com")     # read first
        m, p, body = fake.writes[0]
        self.assertEqual(p, "/orgs/acme.com/users/bob@acme.com")
        self.assertEqual(body["safe_sender_list"], ["old@x.com", "vip@partner.com"])  # appended

    def test_block_then_remove_sender(self):
        from execution.skills import proofpoint_block_sender as bl, proofpoint_remove_sender as rm
        fb = FakePP(get_data={"blocked_sender_list": []})
        bl.run(_ctx(fb), domain="acme.com", email="b@acme.com", sender="spam.com")
        self.assertEqual(fb.writes[0][2]["blocked_sender_list"], ["spam.com"])
        fr = FakePP(get_data={"safe_sender_list": ["bad@x.com", "ok@x.com"]})
        rm.run(_ctx(fr), domain="acme.com", email="b@acme.com", sender="bad@x.com", list="safe")
        self.assertEqual(fr.writes[0][2]["safe_sender_list"], ["ok@x.com"])         # removed

    def test_delete_user_destructive(self):
        from execution.skills import proofpoint_delete_user as du
        fake = FakePP()
        du.run(_ctx(fake), domain="acme.com", email="bob@acme.com")
        self.assertEqual(fake.writes[0], ("DELETE", "/orgs/acme.com/users/bob@acme.com", None))

    def test_get_user_batches_in_one_call(self):
        # D-110: a list of emails is fetched in ONE call; invalid ones become error rows without
        # an HTTP GET, so the agent doesn't call the tool once per user.
        from execution.skills import proofpoint_get_user as gu

        class FP:
            def __init__(self): self.gets = []
            def get(self, path, params=None):
                self.gets.append(path)
                return {"primary_email": path.rsplit("/", 1)[-1], "license": "beginner"}
        fake = FP()
        r = gu.run(_ctx(fake), domain="acme.com",
                   emails=["a@acme.com", "b@acme.com", "bad-email"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["users_checked"], 3)
        self.assertEqual(len(fake.gets), 2)               # invalid email skipped the HTTP call
        by = {x["email"]: x for x in r["results"]}
        self.assertEqual(by["a@acme.com"]["primary_email"], "a@acme.com")
        self.assertFalse(by["bad-email"]["ok"])           # invalid → error row, no GET

    def test_validation(self):
        from execution.skills import proofpoint_get_user as gu, proofpoint_allow_sender as al
        self.assertFalse(gu.run(_ctx(FakePP()), domain="acme.com", email="not-an-email")["ok"])
        self.assertFalse(al.run(_ctx(FakePP()), domain="bad domain", email="b@acme.com",
                                sender="x@y.com")["ok"])

    def test_group_registered(self):
        from execution.core.tool_groups import GROUP_INFO
        self.assertIn("proofpoint", GROUP_INFO)
        self.assertIn("essentials", GROUP_INFO["proofpoint"]["title"].lower())


if __name__ == "__main__":
    unittest.main()
