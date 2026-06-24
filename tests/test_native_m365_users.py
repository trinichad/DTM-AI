"""Native bulk `users=[...]` path on M365 per-user WRITE skills (D-110 family).

One tool call acts on MANY users instead of the agent looping once per person. Each row in
`results` is tagged with its `user`; `users_done` counts them; the single-user path is unchanged.
Fakes mirror tests/test_m365.py (FakeGraph / _graph_ctx, scoped_write recording)."""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


class _SeqGraph:
    """FakeGraph variant whose GET reply for a path-prefix can be a per-user iterator.

    `gets` = {prefix: reply | callable(path) | {upn: reply|callable}}. Records writes/deletes."""
    def __init__(self, gets):
        self.gets = gets
        self.writes = []

    def _upn(self, path):
        seg = path.split("/users/", 1)[1] if "/users/" in path else ""
        return seg.split("/")[0].split("?")[0]

    def get(self, path, params=None):
        bare = path.split("?")[0]
        for prefix, reply in self.gets.items():
            if bare.startswith(prefix):
                if isinstance(reply, dict) and self._upn(path) in reply:
                    r = reply[self._upn(path)]
                    return r(path) if callable(r) else r
                return reply(path) if callable(reply) else reply
        return {"error": f"unexpected GET {path}"}

    def post(self, path, body=None):
        self.writes.append(("POST", path, body))
        return {"ok": True}

    def patch(self, path, body=None):
        self.writes.append(("PATCH", path, body))
        return {"ok": True}

    def delete(self, path, body=None):
        self.writes.append(("DELETE", path, None))
        return {"ok": True}


class SetMfaBatch(unittest.TestCase):
    def _states(self):
        # each user: read 'disabled', then verify 'enforced'
        seq = {}
        for u in ("a@x.com", "b@x.com"):
            it = iter([{"perUserMfaState": "disabled"}, {"perUserMfaState": "enforced"}])
            seq[u] = lambda p, it=it: next(it)
        return seq

    def test_users_list_sets_each_and_tags_user(self):
        from execution.skills import m365_set_mfa
        fake = _SeqGraph({"/beta/users/": self._states()})
        r = m365_set_mfa.run(_ctx(fake), users=["a@x.com", "b@x.com"], state="enforced")
        self.assertTrue(r["ok"])
        self.assertEqual(r["users_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(len(r["results"]), 2)
        self.assertEqual({x["user"] for x in r["results"]}, {"a@x.com", "b@x.com"})
        self.assertTrue(all(x["ok"] for x in r["results"]))

    def test_single_user_path_unchanged(self):
        from execution.skills import m365_set_mfa
        it = iter([{"perUserMfaState": "disabled"}, {"perUserMfaState": "enforced"}])
        fake = _SeqGraph({"/beta/users/": lambda p: next(it)})
        r = m365_set_mfa.run(_ctx(fake), user="solo@x.com", state="enforced")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["user"], "solo@x.com")
        self.assertNotIn("users_done", r)


class RemoveLicenseBatch(unittest.TestCase):
    _SKUS = {"value": [{"skuPartNumber": "O365_BUSINESS_PREMIUM", "skuId": "sku-1",
                        "prepaidUnits": {"enabled": 10}, "consumedUnits": 5}]}

    def _user_states(self):
        seq = {}
        for u in ("a@x.com", "b@x.com"):
            it = iter([{"id": "u", "assignedLicenses": [{"skuId": "sku-1"}]},  # has it
                       {"assignedLicenses": []}])                              # gone after
            seq[u] = lambda p, it=it: next(it)
        return seq

    def test_users_list_removes_each(self):
        from execution.skills import m365_remove_license as rl
        fake = _SeqGraph({"/subscribedSkus": self._SKUS, "/users/": self._user_states()})
        r = rl.run(_ctx(fake), users=["a@x.com", "b@x.com"],
                   license="O365_BUSINESS_PREMIUM")
        self.assertTrue(r["ok"])
        self.assertEqual(r["users_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual({x["user"] for x in r["results"]}, {"a@x.com", "b@x.com"})
        # both POSTed an assignLicense removing sku-1
        posts = [w for w in fake.writes if w[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertTrue(all(w[2]["removeLicenses"] == ["sku-1"] for w in posts))

    def test_single_user_path_unchanged(self):
        from execution.skills import m365_remove_license as rl
        states = iter([{"id": "u", "assignedLicenses": [{"skuId": "sku-1"}]},
                       {"assignedLicenses": []}])
        fake = _SeqGraph({"/subscribedSkus": self._SKUS,
                          "/users/": lambda p: next(states)})
        r = rl.run(_ctx(fake), user="solo@x.com", license="O365_BUSINESS_PREMIUM")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["user"], "solo@x.com")
        self.assertNotIn("users_done", r)


class AddPhoneAuthBatch(unittest.TestCase):
    def _methods(self, number):
        seq = {}
        for u in ("a@x.com", "b@x.com"):
            it = iter([{"value": []},   # none yet
                       {"value": [{"id": "m1", "phoneType": "mobile",
                                   "phoneNumber": number}]}])   # verify shows it
            seq[u] = lambda p, it=it: next(it)
        return seq

    def test_users_list_adds_phone_to_each(self):
        from execution.skills import m365_add_phone_auth as ap
        fake = _SeqGraph({"/users/": self._methods("+1 5551234567")})
        r = ap.run(_ctx(fake), users=["a@x.com", "b@x.com"], phone="555-123-4567")
        self.assertTrue(r["ok"])
        self.assertEqual(r["users_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual({x["user"] for x in r["results"]}, {"a@x.com", "b@x.com"})
        posts = [w for w in fake.writes if w[0] == "POST"]
        self.assertEqual(len(posts), 2)

    def test_single_user_path_unchanged(self):
        from execution.skills import m365_add_phone_auth as ap
        it = iter([{"value": []},
                   {"value": [{"id": "m1", "phoneType": "mobile",
                               "phoneNumber": "+1 5551234567"}]}])
        fake = _SeqGraph({"/users/": lambda p: next(it)})
        r = ap.run(_ctx(fake), user="solo@x.com", phone="555-123-4567")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["user"], "solo@x.com")
        self.assertNotIn("users_done", r)


class SetUserContactBatch(unittest.TestCase):
    """Threads the extra (**fields) per user — same job title applied to a list."""

    def test_users_list_sets_fields_on_each(self):
        from execution.skills import m365_set_user_contact as sc

        class CGraph:
            def __init__(self):
                self.writes = []
                self.pre = {u: iter([{"id": f"id-{u}", "onPremisesSyncEnabled": False}])
                            for u in ("a@x.com", "b@x.com")}

            def get(self, path, params=None):
                seg = path.split("/users/", 1)[1].split("?")[0]
                if seg in self.pre:
                    return next(self.pre[seg])
                return {"id": seg, "jobTitle": "Engineer"}     # verify re-read by uid

            def patch(self, path, body=None):
                self.writes.append(("PATCH", path, body))
                return {"ok": True}
        fake = CGraph()
        r = sc.run(_ctx(fake), users=["a@x.com", "b@x.com"], job_title="Engineer")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["users_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual({x["user"] for x in r["results"]}, {"a@x.com", "b@x.com"})
        self.assertTrue(all("jobTitle" in w[2] for w in fake.writes))

    def test_single_user_path_unchanged(self):
        from execution.skills import m365_set_user_contact as sc

        class CGraph:
            def __init__(self):
                self.pre = iter([{"id": "uid-1", "onPremisesSyncEnabled": False}])

            def get(self, path, params=None):
                seg = path.split("/users/", 1)[1].split("?")[0]
                if seg == "solo@x.com":
                    return next(self.pre)
                return {"id": seg, "jobTitle": "Engineer"}

            def patch(self, path, body=None):
                return {"ok": True}
        r = sc.run(_ctx(CGraph()), user="solo@x.com", job_title="Engineer")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["user"], "solo@x.com")
        self.assertNotIn("users_done", r)


if __name__ == "__main__":
    unittest.main()
