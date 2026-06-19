"""playbooks tests — learned skills as dedup'd, searchable markdown procedures (D-15)."""
import tempfile
import unittest
from pathlib import Path

from execution.core.playbooks import PlaybookStore


class Playbooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PlaybookStore(path=Path(self.tmp.name))   # playbooks land in <path>/skills

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_get(self):
        r = self.store.save("MFA Gap Report", description="Find users without MFA",
                            tools=["entra_list_users"], when="when asked for mfa gaps",
                            steps="1. list users\n2. filter mfa==false", tags=["mfa", "audit"],
                            created_by="alex")
        self.assertTrue(r["ok"])
        self.assertEqual(r["slug"], "mfa-gap-report")
        s = self.store.get("mfa-gap-report")
        self.assertEqual(s["name"], "MFA Gap Report")
        self.assertEqual(s["tools"], ["entra_list_users"])
        self.assertIn("mfa", s["tags"])
        self.assertIn("filter mfa", s["body"])
        self.assertEqual(s["created_by"], "alex")

    def test_requires_name(self):
        with self.assertRaises(ValueError):
            self.store.save("  ")

    def test_dedup_on_slug(self):
        self.store.save("MFA Gap Report", description="find mfa gaps")
        r = self.store.save("mfa gap report", description="anything")   # same slug
        self.assertFalse(r["ok"])
        self.assertEqual(r["duplicate"]["slug"], "mfa-gap-report")

    def test_dedup_on_term_overlap(self):
        self.store.save("Find MFA gaps for a client", description="list users without mfa enabled")
        r = self.store.save("MFA gaps client list users", description="users without mfa enabled")
        self.assertFalse(r["ok"])                      # different name, strong term overlap → dup
        self.assertIn("duplicate", r)

    def test_force_overrides_dedup(self):
        self.store.save("Backup check", description="verify backups")
        r = self.store.save("Backup check", description="verify backups", force=True)
        self.assertTrue(r["ok"])

    def test_search_ranks_matches(self):
        self.store.save("MFA Gap Report", description="users without mfa", tags=["security"])
        self.store.save("Backup Failures", description="failed backups last 24h", tags=["backup"])
        hits = self.store.search("mfa users")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["slug"], "mfa-gap-report")
        self.assertEqual(self.store.search("nonexistent zzz"), [])

    def test_list_and_delete(self):
        self.store.save("One", description="first procedure")
        self.store.save("Two", description="second procedure")
        self.assertEqual(len(self.store.list_skills()), 2)
        self.store.delete("one")
        self.assertEqual(len(self.store.list_skills()), 1)
        with self.assertRaises(ValueError):
            self.store.delete("ghost")

    def test_empty_library_is_safe(self):
        self.assertEqual(self.store.list_skills(), [])
        self.assertEqual(self.store.search("anything"), [])
        self.assertIsNone(self.store.get("nope"))

    def test_skill_search_tool(self):
        self.store.save("MFA Gap Report", description="users without mfa")
        from execution.skills import skill_search
        orig = skill_search.PlaybookStore                      # the tool binds it at import time
        skill_search.PlaybookStore = lambda *a, **k: self.store
        try:
            out = skill_search.run(ctx=None, query="mfa users")
        finally:
            skill_search.PlaybookStore = orig
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["matches"][0]["slug"], "mfa-gap-report")


if __name__ == "__main__":
    unittest.main()
