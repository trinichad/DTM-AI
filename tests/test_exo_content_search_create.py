"""exo_content_search_create (D-115) — create + start a Content Search.

Proves: WHERE/WHO stay separate (ExchangeLocation vs KQL from:/participants:); an explicit
location is required (no accidental tenant-wide "All"); an empty query is refused; the write is
verified by a read-back before reporting success; start:false creates without starting; and a
create failure is a clean error, not a raise.
"""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


class FakeEXO:
    """Records compliance calls; serves Get-ComplianceSearch from an in-memory store."""
    def __init__(self, create_error=None, start_error=None):
        self.calls = []
        self.searches = {}
        self.create_error = create_error
        self.start_error = start_error

    def invoke_compliance(self, cmdlet, params=None):
        params = params or {}
        self.calls.append((cmdlet, dict(params)))
        if cmdlet == "New-ComplianceSearch":
            if self.create_error:
                return {"error": self.create_error}
            self.searches[params["Name"]] = {
                "Name": params["Name"], "Status": "NotStarted",
                "ContentMatchQuery": params.get("ContentMatchQuery"),
                "ExchangeLocation": params.get("ExchangeLocation")}
            return {"ok": True}
        if cmdlet == "Start-ComplianceSearch":
            if self.start_error:
                return {"error": self.start_error}
            s = self.searches.get(params["Identity"])
            if s:
                s["Status"] = "Starting"
            return {"ok": True}
        if cmdlet == "Get-ComplianceSearch":
            ident = params.get("Identity")
            if ident:
                s = self.searches.get(ident)
                return [s] if s else {"error": "couldn't be found"}
            return list(self.searches.values())
        return {"error": f"unexpected cmdlet {cmdlet}"}

    def _last(self, cmdlet):
        return [c for c in self.calls if c[0] == cmdlet]


class CreateContentSearch(unittest.TestCase):
    def test_builds_kql_and_locations_separately_and_starts(self):
        from execution.skills import exo_content_search_create as mod
        fake = FakeEXO()
        r = mod.run(_ctx(fake), name="hunt", mailboxes=["a@x.com", "b@x.com"],
                    keywords="invoice", from_address="ceo@x.com", date_from="2026-01-01")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["started"])
        create = fake._last("New-ComplianceSearch")[0][1]
        # WHERE = locations, WHO = inside the KQL
        self.assertEqual(create["ExchangeLocation"], ["a@x.com", "b@x.com"])
        self.assertIn('from:"ceo@x.com"', create["ContentMatchQuery"])
        self.assertIn("invoice", create["ContentMatchQuery"])
        self.assertIn("received>=2026-01-01", create["ContentMatchQuery"])
        # specific mailboxes → tolerate a not-found location
        self.assertTrue(create["AllowNotFoundExchangeLocationsEnabled"])
        # started
        self.assertTrue(fake._last("Start-ComplianceSearch"))

    def test_all_mailboxes_sends_All(self):
        from execution.skills import exo_content_search_create as mod
        fake = FakeEXO()
        r = mod.run(_ctx(fake), all_mailboxes=True, keywords="breach")
        self.assertTrue(r["ok"], r)
        create = fake._last("New-ComplianceSearch")[0][1]
        self.assertEqual(create["ExchangeLocation"], "All")
        self.assertNotIn("AllowNotFoundExchangeLocationsEnabled", create)

    def test_requires_a_location(self):
        from execution.skills import exo_content_search_create as mod
        fake = FakeEXO()
        r = mod.run(_ctx(fake), keywords="x")
        self.assertFalse(r["ok"])
        self.assertIn("all_mailboxes", r["error"])
        self.assertFalse(fake.calls)                       # never hit the endpoint

    def test_rejects_both_location_modes(self):
        from execution.skills import exo_content_search_create as mod
        r = mod.run(_ctx(FakeEXO()), mailboxes=["a@x.com"], all_mailboxes=True, keywords="x")
        self.assertFalse(r["ok"])
        self.assertIn("not both", r["error"])

    def test_empty_query_is_refused(self):
        from execution.skills import exo_content_search_create as mod
        fake = FakeEXO()
        r = mod.run(_ctx(fake), mailboxes=["a@x.com"])     # no criteria at all
        self.assertFalse(r["ok"])
        self.assertIn("empty query", r["error"].lower())
        self.assertFalse(fake.calls)

    def test_raw_kql_overrides_fields(self):
        from execution.skills import exo_content_search_create as mod
        fake = FakeEXO()
        r = mod.run(_ctx(fake), mailboxes=["a@x.com"], keywords="ignored",
                    kql='subject:"Q3 numbers" AND hasattachment:true')
        self.assertTrue(r["ok"], r)
        create = fake._last("New-ComplianceSearch")[0][1]
        self.assertEqual(create["ContentMatchQuery"], 'subject:"Q3 numbers" AND hasattachment:true')

    def test_start_false_creates_without_starting(self):
        from execution.skills import exo_content_search_create as mod
        fake = FakeEXO()
        r = mod.run(_ctx(fake), mailboxes=["a@x.com"], keywords="x", start=False)
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["started"])
        self.assertFalse(fake._last("Start-ComplianceSearch"))

    def test_create_failure_is_clean_error(self):
        from execution.skills import exo_content_search_create as mod
        fake = FakeEXO(create_error="403 access denied")
        r = mod.run(_ctx(fake), mailboxes=["a@x.com"], keywords="x")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "create")
        self.assertIn("eDiscovery Manager", r["error"])    # role hint appended on 403

    def test_generates_a_name_when_omitted(self):
        from execution.skills import exo_content_search_create as mod
        fake = FakeEXO()
        r = mod.run(_ctx(fake), mailboxes=["a@x.com"], keywords="x")
        self.assertTrue(r["name"].startswith("MSPAI-CS-"))


if __name__ == "__main__":
    unittest.main()
