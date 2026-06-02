"""HermesSkillsReader tests — parse the skills tree, tolerate a missing dir."""
import tempfile
import unittest
from pathlib import Path

from execution.core.hermes_skills import HermesSkillsReader


class Reader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _skill(self, rel, name, desc):
        d = self.root / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\nbody\n", encoding="utf-8")

    def test_missing_dir_is_clean(self):
        r = HermesSkillsReader(self.root / "nope")
        self.assertFalse(r.available)
        self.assertEqual(r.list_skills(), [])

    def test_permission_error_is_tolerated(self):
        # regression: systemd ProtectHome makes ~/.hermes raise PermissionError on exists();
        # that must read as "not available", not 500 the integrations endpoint.
        import unittest.mock as mock
        r = HermesSkillsReader(self.root)
        r.root = mock.Mock()
        r.root.exists.side_effect = PermissionError(13, "Permission denied")
        self.assertFalse(r.available)
        self.assertEqual(r.list_skills(), [])

    def test_parses_skills_with_category(self):
        self._skill("security/posture", "posture-report", "summarize posture")
        self._skill("network/sweep", "stale-sweep", "find stale agents")
        r = HermesSkillsReader(self.root)
        self.assertTrue(r.available)
        skills = r.list_skills()
        self.assertEqual(len(skills), 2)
        names = {s["name"] for s in skills}
        self.assertEqual(names, {"posture-report", "stale-sweep"})
        posture = next(s for s in skills if s["name"] == "posture-report")
        self.assertEqual(posture["category"], "security")
        self.assertEqual(posture["description"], "summarize posture")

    def test_frontmatterless_skill_falls_back(self):
        d = self.root / "general" / "raw"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# Raw Skill\nFirst line here.\n", encoding="utf-8")
        s = HermesSkillsReader(self.root).list_skills()[0]
        self.assertEqual(s["name"], "raw")          # falls back to dir name
        self.assertEqual(s["description"], "Raw Skill")  # first heading/line

    def test_example_skills_dir_loads(self):
        # the committed examples used by the dev preview
        ex = Path(__file__).resolve().parents[1] / "examples" / "hermes-skills"
        skills = HermesSkillsReader(ex).list_skills()
        self.assertTrue(any(s["name"] == "client-posture-report" for s in skills))


if __name__ == "__main__":
    unittest.main()
