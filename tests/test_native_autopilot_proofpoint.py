"""Native bulk/list params on Autopilot + Proofpoint per-user tools (D-110).

Each tool gained an array param alongside its single param so the agent acts on MANY
in ONE call. These tests exercise the list path for 2 items (length, *_done count,
per-row attribution) and confirm the single path is unchanged.
"""
import base64
import unittest

from execution.core.context import ToolContext


# ── Autopilot fakes (mirror test_m365.FakeGraph, plus a stateful delete) ──────────────────────
class FakeGraph:
    """Canned GET replies by path-prefix; records writes/deletes."""
    def __init__(self, gets=None):
        self.gets = gets or {}
        self.writes = []

    def get(self, path, params=None):
        for prefix, reply in self.gets.items():
            if path.split("?")[0].startswith(prefix):
                return reply(path, params) if callable(reply) else reply
        return {"error": f"unexpected GET {path}"}

    def post(self, path, body=None):
        self.writes.append(("POST", path, body))
        return {"id": "u-1", **(body or {})}

    def patch(self, path, body=None):
        self.writes.append(("PATCH", path, body))
        return {"ok": True}

    def delete(self, path, body=None):
        self.writes.append(("DELETE", path, None))
        return {"ok": True}


def _graph_ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


# ── Proofpoint fakes (mirror test_proofpoint.FakePP) ──────────────────────────────────────────
class FakePP:
    def __init__(self, get_data=None):
        self.get_data = get_data if get_data is not None else {}
        self.gets, self.writes = [], []

    def get(self, path, params=None):
        self.gets.append((path, params))
        return self.get_data

    def write(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"ok": True, "primary_email": path.rsplit("/", 1)[-1], **(body or {})}

    def write_destructive(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"ok": True}


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda integ, tenant: fake)


class AutopilotListNative(unittest.TestCase):
    def test_serials_list_batches_in_one_call(self):
        from execution.skills import m365_list_autopilot_devices as ld

        def reply(path, params):
            # one server filter try per serial — return the matching device
            serial = "ABC123" if "ABC123" in (params or {}).get("$filter", "") else "ZZZ999"
            return {"value": [{"serialNumber": serial, "id": f"d-{serial}"}]}
        fake = FakeGraph({"/deviceManagement/windowsAutopilotDeviceIdentities": reply})
        r = ld.run(_graph_ctx(fake), serials=["ABC123", "ZZZ999"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["devices_done"], 2)
        self.assertEqual(len(r["results"]), 2)
        self.assertEqual(r["ok_count"], 2)
        # per-row attribution: each row searched for its own serial
        searched = sorted(row["searched_for"] for row in r["results"])
        self.assertEqual(searched, ["ABC123", "ZZZ999"])

    def test_single_path_unchanged(self):
        from execution.skills import m365_list_autopilot_devices as ld
        fake = FakeGraph({"/deviceManagement/windowsAutopilotDeviceIdentities":
                          {"value": [{"serialNumber": "ABC123", "id": "d-1"}]}})
        r = ld.run(_graph_ctx(fake), serial="ABC123")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["devices"][0]["serial"], "ABC123")
        self.assertNotIn("results", r)                     # not the batch shape


class AutopilotRemoveNative(unittest.TestCase):
    class StatefulGraph(FakeGraph):
        """Returns each serial's device until it is DELETEd, then nothing (verify passes)."""
        def __init__(self):
            super().__init__()
            self.deleted = set()

        def get(self, path, params=None):
            if path.startswith("/deviceManagement/windowsAutopilotDeviceIdentities"):
                flt = (params or {}).get("$filter", "")
                out = []
                for s in ("S1", "S2"):
                    if s in flt and f"d-{s}" not in self.deleted:
                        out.append({"serialNumber": s, "id": f"d-{s}"})
                return {"value": out}
            return {"error": f"unexpected GET {path}"}

        def delete(self, path, body=None):
            self.deleted.add(path.rsplit("/", 1)[-1])
            self.writes.append(("DELETE", path, None))
            return {"ok": True}

    def test_serials_list_removes_and_verifies_each(self):
        from execution.skills import m365_remove_autopilot_device as rd
        fake = self.StatefulGraph()
        r = rd.run(_graph_ctx(fake), serials=["S1", "S2"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["removals_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(len(r["results"]), 2)
        removed = sorted(row["serial_removed"] for row in r["results"])
        self.assertEqual(removed, ["S1", "S2"])
        self.assertEqual(sorted(p for _, p, _ in fake.writes),
                         ["/deviceManagement/windowsAutopilotDeviceIdentities/d-S1",
                          "/deviceManagement/windowsAutopilotDeviceIdentities/d-S2"])

    def test_single_path_unchanged(self):
        from execution.skills import m365_remove_autopilot_device as rd
        fake = self.StatefulGraph()
        r = rd.run(_graph_ctx(fake), serial="S1")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["serial_removed"], "S1")
        self.assertNotIn("results", r)


class ProofpointAllowSenderNative(unittest.TestCase):
    def test_emails_list_same_sender_many_users(self):
        from execution.skills import proofpoint_allow_sender as al
        fake = FakePP(get_data={"safe_sender_list": []})
        r = al.run(_ctx(fake), domain="acme.com",
                   emails=["a@acme.com", "b@acme.com"], sender="vip@partner.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["users_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(len(r["results"]), 2)
        users = sorted(row["user"] for row in r["results"])
        self.assertEqual(users, ["a@acme.com", "b@acme.com"])
        # read-then-write sequence preserved per item: a GET then a PUT for each user
        self.assertEqual(len(fake.gets), 2)
        self.assertEqual(len(fake.writes), 2)
        self.assertTrue(all(row["allowed"] == "vip@partner.com" for row in r["results"]))

    def test_invalid_email_becomes_error_row(self):
        from execution.skills import proofpoint_allow_sender as al
        fake = FakePP(get_data={"safe_sender_list": []})
        r = al.run(_ctx(fake), domain="acme.com",
                   emails=["good@acme.com", "bad-email"], sender="vip@partner.com")
        by = {row["user"]: row for row in r["results"]}
        self.assertTrue(by["good@acme.com"]["ok"])
        self.assertFalse(by["bad-email"]["ok"])
        self.assertEqual(len(fake.writes), 1)              # invalid skipped the mutate

    def test_single_path_unchanged(self):
        from execution.skills import proofpoint_allow_sender as al
        fake = FakePP(get_data={"safe_sender_list": ["old@x.com"]})
        r = al.run(_ctx(fake), domain="acme.com", email="bob@acme.com", sender="vip@partner.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["user"], "bob@acme.com")
        self.assertNotIn("results", r)
        self.assertEqual(fake.writes[0][2]["safe_sender_list"], ["old@x.com", "vip@partner.com"])


class ProofpointUpdateUserNative(unittest.TestCase):
    def test_emails_list_same_changes_many_users(self):
        from execution.skills import proofpoint_update_user as uu
        fake = FakePP()
        r = uu.run(_ctx(fake), domain="acme.com",
                   emails=["a@acme.com", "b@acme.com"], active=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["users_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(len(r["results"]), 2)
        paths = sorted(p for _, p, _ in fake.writes)
        self.assertEqual(paths, ["/orgs/acme.com/users/a@acme.com",
                                 "/orgs/acme.com/users/b@acme.com"])
        self.assertTrue(all(body == {"is_active": False} for _, _, body in fake.writes))

    def test_single_path_unchanged(self):
        from execution.skills import proofpoint_update_user as uu
        fake = FakePP()
        r = uu.run(_ctx(fake), domain="acme.com", email="bob@acme.com", active=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0], ("PUT", "/orgs/acme.com/users/bob@acme.com",
                                          {"is_active": False}))
        self.assertNotIn("results", r)


class ProofpointDeleteUserNative(unittest.TestCase):
    def test_emails_list_deletes_each(self):
        from execution.skills import proofpoint_delete_user as du
        fake = FakePP()
        r = du.run(_ctx(fake), domain="acme.com", emails=["a@acme.com", "b@acme.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["users_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(len(r["results"]), 2)
        users = sorted(row["user"] for row in r["results"])
        self.assertEqual(users, ["a@acme.com", "b@acme.com"])
        self.assertEqual(sorted(p for _, p, _ in fake.writes),
                         ["/orgs/acme.com/users/a@acme.com", "/orgs/acme.com/users/b@acme.com"])
        self.assertTrue(all(m == "DELETE" for m, _, _ in fake.writes))

    def test_single_path_unchanged(self):
        from execution.skills import proofpoint_delete_user as du
        fake = FakePP()
        r = du.run(_ctx(fake), domain="acme.com", email="bob@acme.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0], ("DELETE", "/orgs/acme.com/users/bob@acme.com", None))
        self.assertNotIn("results", r)


if __name__ == "__main__":
    unittest.main()
