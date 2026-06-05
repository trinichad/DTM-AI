"""hermes_agents tests — read the profile team, counts, SOUL edit, path-safety."""
import tempfile
import unittest
from pathlib import Path

from execution.core.hermes_agents import (
    create_agent, delete_agent, get_agent, list_agents, read_memory, set_soul,
)


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

    def test_create_agent(self):
        a = create_agent("reportwright", description="Builds reports", role="Reporter",
                         cfg=self.cfg)
        self.assertEqual(a["id"], "reportwright")
        self.assertEqual(a["role"], "Reporter")
        self.assertEqual(a["description"], "Builds reports")
        self.assertFalse(a["is_manager"])
        self.assertTrue(a["soul_present"])
        # inherited the manager's brain config (cloud)
        self.assertEqual(a["brain"]["mode"], "cloud")
        # fresh — no memory/skills/sessions
        self.assertEqual(a["memories"], 0)
        self.assertEqual(a["sessions"], 0)
        # now visible to the reader
        self.assertIn("reportwright", [x["id"] for x in list_agents(self.cfg)])
        # dirs exist
        pd = self.d / "profiles" / "reportwright"
        for sub in ("memories", "sessions", "skills"):
            self.assertTrue((pd / sub).is_dir())

    def test_create_with_custom_soul(self):
        a = create_agent("custom", soul="# Custom\n- name: Custom Bot\n", cfg=self.cfg)
        self.assertEqual(a["name"], "Custom Bot")
        self.assertIn("Custom Bot", get_agent("custom", self.cfg)["soul"])

    def test_create_default_rejected(self):
        with self.assertRaises(ValueError):
            create_agent("default", cfg=self.cfg)

    def test_create_duplicate_rejected(self):
        with self.assertRaises(FileExistsError):
            create_agent("patchwright", cfg=self.cfg)

    def test_create_bad_name_rejected(self):
        with self.assertRaises(ValueError):
            create_agent("../evil", cfg=self.cfg)

    def test_create_description_yaml_safe(self):
        # an apostrophe in the description must not break profile.yaml parsing
        a = create_agent("apos", description="Acme's reporter", cfg=self.cfg)
        self.assertEqual(a["description"], "Acme's reporter")

    def test_delete_agent(self):
        create_agent("temp", cfg=self.cfg)
        res = delete_agent("temp", self.cfg)
        self.assertTrue(res["deleted"])
        self.assertIsNone(get_agent("temp", self.cfg))
        self.assertNotIn("temp", [x["id"] for x in list_agents(self.cfg)])

    def test_delete_default_rejected(self):
        with self.assertRaises(ValueError):
            delete_agent("default", self.cfg)
        self.assertTrue((self.d / "SOUL.md").exists())   # manager untouched

    def test_delete_unknown_rejected(self):
        with self.assertRaises(FileNotFoundError):
            delete_agent("ghost", self.cfg)

    def test_delete_cleans_alias_and_logs(self):
        create_agent("withextras", cfg=self.cfg)
        alias = self.d / ".local" / "bin" / "withextras"
        alias.parent.mkdir(parents=True, exist_ok=True); alias.write_text("#!/bin/sh\n")
        glog = self.d / "logs" / "gateways" / "withextras"
        glog.mkdir(parents=True, exist_ok=True); (glog / "g.log").write_text("x")
        delete_agent("withextras", self.cfg)
        self.assertFalse(alias.exists())
        self.assertFalse(glog.exists())


if __name__ == "__main__":
    unittest.main()
