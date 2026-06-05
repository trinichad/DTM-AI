"""hermes_agents tests — read the profile team, counts, SOUL edit, path-safety."""
import tempfile
import unittest
from pathlib import Path

from execution.core.hermes_agents import get_agent, list_agents, read_memory, set_soul


class StubCfg:
    def __init__(self, d): self.d = d
    def get(self, k, default=None): return self.d.get(k, default)


def write(p: Path, t: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(t)


CLOUD = "model:\n  default: gpt-5.5\n  provider: openai-codex\n  base_url: x\n"


class Agents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        # manager = default profile at the data root
        write(self.d / "SOUL.md", "# AtlasOps\n## Identity\n- name: AtlasOps Manager\n- role: Director\n")
        write(self.d / "config.yaml", CLOUD)
        (self.d / "memories").mkdir(); (self.d / "sessions").mkdir()
        # a specialist with 2 memories + 1 skill + a local brain
        pp = self.d / "profiles" / "patchwright"
        write(pp / "SOUL.md", "# Patchwright\n## Identity\n- name: Patchwright\n- role: Kaseya Engineer\n")
        write(pp / "config.yaml", "model:\n  default: qwen3.5:27b\n  provider: custom\n  base_url: y\n")
        write(pp / "profile.yaml", "description: 'Kaseya ops'\n")
        write(pp / "MEMORY.md", "# Memory\n- learned a thing\n- learned another\n")
        write(pp / "USER.md", "DTM Consulting MSP team.\n")
        write(pp / "skills" / "cat" / "s" / "SKILL.md", "x")
        self.cfg = StubCfg({"DTM_HERMES_DATA_DIR": str(self.d)})

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_manager_first_and_counts(self):
        a = list_agents(self.cfg)
        self.assertEqual(a[0]["id"], "default")
        self.assertTrue(a[0]["is_manager"])
        self.assertEqual(a[0]["name"], "AtlasOps Manager")
        pw = next(x for x in a if x["id"] == "patchwright")
        self.assertEqual(pw["role"], "Kaseya Engineer")
        self.assertEqual(pw["description"], "Kaseya ops")
        self.assertEqual(pw["memories"], 2)                # MEMORY.md non-heading lines
        self.assertEqual(pw["skills"], 1)
        self.assertEqual(pw["brain"]["mode"], "local")     # per-agent brain read

    def test_read_memory(self):
        m = read_memory("patchwright", self.cfg)
        self.assertIn("learned a thing", m["memory"])
        self.assertIn("DTM Consulting", m["user"])
        self.assertIsNone(read_memory("nope", self.cfg))

    def test_get_and_edit_soul(self):
        self.assertIn("Patchwright", get_agent("patchwright", self.cfg)["soul"])
        set_soul("patchwright", "# new\n- name: PW2\n", self.cfg)
        self.assertIn("new", get_agent("patchwright", self.cfg)["soul"])

    def test_unknown_returns_none(self):
        self.assertIsNone(get_agent("nope", self.cfg))

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            set_soul("../etc", "x", self.cfg)


if __name__ == "__main__":
    unittest.main()
