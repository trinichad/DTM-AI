"""exo_content_search_preview (D-115) — idempotent preview lifecycle.

Proves the four states: (1) no preview yet + search Completed → starts a preview;
(2) search estimate not done → "wait", no preview started; (3) preview Completed → parsed sample
items + raw included; (4) preview running → "still preparing". Also: a missing search is a clean
error, and -Export is never sent (only SearchName + Preview).
"""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


_RESULTS = ("Location: a@x.com; Sender: ceo@x.com; Subject: Q3 numbers, final; Type: Email; "
            "Size: 15269; Received Time: 2026-03-01 10:50:00; Data Link: abc123; "
            "Location: b@x.com; Sender: cfo@x.com; Subject: Re: budget; Type: Email; "
            "Size: 2048; Received Time: 2026-03-02 09:00:00; Data Link: def456")


class FakeEXO:
    def __init__(self, search_status="Completed", action=None):
        self.calls = []
        self.search_status = search_status
        self.action = action               # None = no preview action exists yet

    def invoke_compliance(self, cmdlet, params=None):
        params = params or {}
        self.calls.append((cmdlet, dict(params)))
        if cmdlet == "Get-ComplianceSearchAction":
            return [self.action] if self.action else {"error": "the action wasn't found"}
        if cmdlet == "Get-ComplianceSearch":
            return [{"Name": params.get("Identity"), "Status": self.search_status}] \
                if self.search_status else {"error": "couldn't be found"}
        if cmdlet == "New-ComplianceSearchAction":
            return {"ok": True}
        return {"error": f"unexpected cmdlet {cmdlet}"}

    def _last(self, cmdlet):
        return [c for c in self.calls if c[0] == cmdlet]


class PreviewContentSearch(unittest.TestCase):
    def test_starts_preview_when_search_complete_and_no_action(self):
        from execution.skills import exo_content_search_preview as mod
        fake = FakeEXO(search_status="Completed", action=None)
        r = mod.run(_ctx(fake), name="hunt")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["preview_status"], "Starting")
        start = fake._last("New-ComplianceSearchAction")
        self.assertTrue(start)
        # only SearchName + Preview — never -Export
        self.assertEqual(set(start[0][1]), {"SearchName", "Preview"})
        self.assertTrue(start[0][1]["Preview"])

    def test_waits_when_estimate_not_done(self):
        from execution.skills import exo_content_search_preview as mod
        fake = FakeEXO(search_status="InProgress", action=None)
        r = mod.run(_ctx(fake), name="hunt")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["search_status"], "InProgress")
        self.assertFalse(fake._last("New-ComplianceSearchAction"))   # did NOT start a preview

    def test_returns_parsed_items_when_preview_complete(self):
        from execution.skills import exo_content_search_preview as mod
        fake = FakeEXO(action={"Status": "Completed", "Results": _RESULTS})
        r = mod.run(_ctx(fake), name="hunt")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["preview_status"], "Completed")
        self.assertEqual(r["item_count"], 2)
        first = r["items"][0]
        self.assertEqual(first["mailbox"], "a@x.com")
        self.assertEqual(first["sender"], "ceo@x.com")
        # subject with an embedded comma survives (stops at next KNOWN key, not the comma)
        self.assertEqual(first["subject"], "Q3 numbers, final")
        self.assertEqual(first["size_bytes"], 15269)
        self.assertIn("raw_results", r)                    # raw always included
        self.assertFalse(fake._last("New-ComplianceSearchAction"))   # didn't re-start

    def test_still_preparing_when_action_incomplete(self):
        from execution.skills import exo_content_search_preview as mod
        fake = FakeEXO(action={"Status": "InProgress", "Results": ""})
        r = mod.run(_ctx(fake), name="hunt")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["preview_status"], "InProgress")

    def test_missing_search_is_clean_error(self):
        from execution.skills import exo_content_search_preview as mod
        fake = FakeEXO(search_status=None, action=None)
        r = mod.run(_ctx(fake), name="ghost")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "locate")


if __name__ == "__main__":
    unittest.main()
