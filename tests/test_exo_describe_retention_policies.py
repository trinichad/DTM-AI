"""exo_describe_retention_policies (D-114 follow-up) — policies with tags expanded inline.

Proves: each policy's RetentionPolicyTagLinks are joined against the tag list so the result carries
each tag's action/age/scope + a plain-English summary; a single `name` filters to one policy; a
linked tag that no longer exists is surfaced under unresolved_tags rather than dropped silently.
"""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


_TAGS = [
    {"Name": "Archive 2yr", "Type": "All", "RetentionAction": "MoveToArchive",
     "AgeLimitForRetention": 730, "RetentionEnabled": True},
    {"Name": "Del Deleted 6mo", "Type": "DeletedItems", "RetentionAction": "DeleteAndAllowRecovery",
     "AgeLimitForRetention": 180, "RetentionEnabled": True},
    {"Name": "Purge 7yr", "Type": "All", "RetentionAction": "PermanentlyDelete",
     "AgeLimitForRetention": 2555, "RetentionEnabled": True},
]
_POLICIES = [
    {"Name": "RHO Executive", "IsDefault": False,
     "RetentionPolicyTagLinks": ["Archive 2yr", "Del Deleted 6mo"]},
    {"Name": "RHO PM", "IsDefault": True,
     "RetentionPolicyTagLinks": ["Archive 2yr", "Purge 7yr", "Ghost Tag"]},
]


class FakeEXO:
    def __init__(self, policies=_POLICIES, tags=_TAGS):
        self.policies = policies
        self.tags = tags
        self.calls = []

    def invoke(self, cmdlet, params=None):
        self.calls.append((cmdlet, dict(params or {})))
        if cmdlet == "Get-RetentionPolicy":
            ident = str((params or {}).get("Identity", "")).lower()
            if ident:
                hit = [p for p in self.policies if str(p["Name"]).lower() == ident]
                return hit if hit else {"error": "couldn't be found"}
            return list(self.policies)
        if cmdlet == "Get-RetentionPolicyTag":
            return list(self.tags)
        return {"error": f"unexpected {cmdlet}"}


class Describe(unittest.TestCase):
    def test_expands_tags_with_action_age_and_summary(self):
        from execution.skills import exo_describe_retention_policies as t
        r = t.run(_ctx(FakeEXO()))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["count"], 2)
        ex = next(p for p in r["policies"] if p["name"] == "RHO Executive")
        self.assertEqual(ex["tag_count"], 2)
        names = [tag["name"] for tag in ex["tags"]]
        self.assertEqual(names, ["Archive 2yr", "Del Deleted 6mo"])
        archive = ex["tags"][0]
        self.assertEqual(archive["action"], "MoveToArchive")
        self.assertEqual(archive["age_days"], 730)
        self.assertIn("move to archive @ 730d", archive["summary"])
        self.assertIn("move to archive @ 730d", ex["summary"])
        self.assertIn("delete (recoverable) @ 180d", ex["summary"])

    def test_permanent_delete_is_flagged_in_label(self):
        from execution.skills import exo_describe_retention_policies as t
        r = t.run(_ctx(FakeEXO()))
        pm = next(p for p in r["policies"] if p["name"] == "RHO PM")
        self.assertIn("delete (PERMANENT) @ 2555d", pm["summary"])

    def test_unresolved_link_is_surfaced_not_dropped(self):
        from execution.skills import exo_describe_retention_policies as t
        r = t.run(_ctx(FakeEXO()))
        pm = next(p for p in r["policies"] if p["name"] == "RHO PM")
        self.assertEqual(pm["tag_count"], 3)          # counts the link
        self.assertEqual(len(pm["tags"]), 2)          # only 2 resolved
        self.assertEqual(pm["unresolved_tags"], ["Ghost Tag"])

    def test_name_filters_to_one_policy(self):
        from execution.skills import exo_describe_retention_policies as t
        fake = FakeEXO()
        r = t.run(_ctx(fake), name="RHO Executive")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["policies"][0]["name"], "RHO Executive")
        getpol = [c for c in fake.calls if c[0] == "Get-RetentionPolicy"][0]
        self.assertEqual(getpol[1]["Identity"], "RHO Executive")

    def test_missing_named_policy_is_clean_error(self):
        from execution.skills import exo_describe_retention_policies as t
        r = t.run(_ctx(FakeEXO()), name="Nope")
        self.assertFalse(r["ok"])
        self.assertIn("Nope", r["error"])


if __name__ == "__main__":
    unittest.main()
