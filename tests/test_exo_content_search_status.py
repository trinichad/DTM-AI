"""exo_content_search_status (D-115) — status / estimate of a Content Search.

Proves: single-search mode returns Status + estimate + humanized size + per-mailbox breakdown
parsed from SuccessResults; list mode (no name) returns a compact row per search; a missing named
search is a clean error.
"""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


_SUCCESS = ("Location: a@x.com; Item count: 25; Total size: 1048576; "
            "Location: b@x.com; Item count: 3; Total size: 2048")


class FakeEXO:
    def __init__(self, searches):
        self.searches = searches

    def invoke_compliance(self, cmdlet, params=None):
        params = params or {}
        if cmdlet == "Get-ComplianceSearch":
            ident = params.get("Identity")
            if ident:
                s = self.searches.get(ident)
                return [s] if s else {"error": "the search couldn't be found"}
            return list(self.searches.values())
        return {"error": f"unexpected cmdlet {cmdlet}"}


def _store():
    return {"hunt": {"Name": "hunt", "Status": "Completed", "Items": 28, "Size": 1050624,
                     "ContentMatchQuery": 'from:"ceo@x.com"',
                     "ExchangeLocation": ["a@x.com", "b@x.com"], "SuccessResults": _SUCCESS},
            "old": {"Name": "old", "Status": "Completed", "Items": 0, "Size": 0}}


class StatusContentSearch(unittest.TestCase):
    def test_single_search_estimate_and_breakdown(self):
        from execution.skills import exo_content_search_status as mod
        r = mod.run(_ctx(FakeEXO(_store())), name="hunt")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"], "Completed")
        self.assertEqual(r["items"], 28)
        self.assertEqual(r["size_bytes"], 1050624)
        self.assertTrue(r["size"].endswith("MB"))          # humanized
        by = {row["mailbox"]: row for row in r["by_mailbox"]}
        self.assertEqual(by["a@x.com"]["items"], 25)
        self.assertEqual(by["a@x.com"]["size_bytes"], 1048576)
        self.assertEqual(by["b@x.com"]["items"], 3)

    def test_list_mode_when_no_name(self):
        from execution.skills import exo_content_search_status as mod
        r = mod.run(_ctx(FakeEXO(_store())))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["count"], 2)
        names = {s["name"] for s in r["searches"]}
        self.assertEqual(names, {"hunt", "old"})

    def test_missing_named_search_is_clean_error(self):
        from execution.skills import exo_content_search_status as mod
        r = mod.run(_ctx(FakeEXO(_store())), name="ghost")
        self.assertFalse(r["ok"])
        self.assertIn("no content search named 'ghost'", r["error"])


if __name__ == "__main__":
    unittest.main()
