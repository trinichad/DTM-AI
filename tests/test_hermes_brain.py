"""hermes_brain tests — swap the model: block cloud↔local without disturbing the rest."""
import tempfile
import unittest
from pathlib import Path

from execution.core.hermes_brain import get_brain_mode, set_brain_mode


class StubCfg:
    def __init__(self, d): self.d = d
    def get(self, k, default=None): return self.d.get(k, default)


CONFIG = (
    "model:\n  default: gpt-5.5\n  provider: openai-codex\n  base_url: https://x/codex\n"
    "providers: {}\n"
    "mcp_servers:\n  dtm_all:\n    url: http://127.0.0.1:8089/mcp\n"
)


class BrainSwap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "config.yaml").write_text(CONFIG)
        self.cfg = StubCfg({"DTM_HERMES_DATA_DIR": str(self.dir)})

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_cloud(self):
        s = get_brain_mode(self.cfg)
        self.assertEqual(s["mode"], "cloud")
        self.assertEqual(s["model"], "gpt-5.5")
        self.assertEqual(s["provider"], "openai-codex")

    def test_swap_to_local_and_back_preserves_rest(self):
        s = set_brain_mode("local", self.cfg)
        self.assertEqual(s["mode"], "local")
        self.assertEqual(s["provider"], "custom")
        self.assertIn("qwen", s["model"])
        text = (self.dir / "config.yaml").read_text()
        self.assertIn("mcp_servers:", text)          # other top-level keys untouched
        self.assertIn("providers: {}", text)
        self.assertNotIn("gpt-5.5", text)            # model block fully replaced
        s2 = set_brain_mode("cloud", self.cfg)
        self.assertEqual(s2["mode"], "cloud")
        self.assertEqual(s2["model"], "gpt-5.5")
        self.assertIn("mcp_servers:", (self.dir / "config.yaml").read_text())

    def test_env_overrides_local_model(self):
        cfg = StubCfg({"DTM_HERMES_DATA_DIR": str(self.dir), "HERMES_LOCAL_MODEL": "llama3:70b"})
        s = set_brain_mode("local", cfg)
        self.assertEqual(s["model"], "llama3:70b")

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            set_brain_mode("nope", self.cfg)


if __name__ == "__main__":
    unittest.main()
