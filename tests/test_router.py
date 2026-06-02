"""Router tests — prove local-first / cloud-gating (D-3, Behavioral Rule #5)."""
import tempfile
import unittest
from pathlib import Path

from execution.core.config import Config
from execution.core.router import ClaudeProvider, ModelRouter, OllamaProvider


def _cfg(**pairs) -> Config:
    """Build a Config from a temp .env with given key=value pairs (0600)."""
    d = tempfile.mkdtemp()
    p = Path(d) / ".env"
    p.write_text("\n".join(f"{k}={v}" for k, v in pairs.items()), encoding="utf-8")
    p.chmod(0o600)
    return Config(env_path=p)


class Routing(unittest.TestCase):
    def test_default_is_local(self):
        r = ModelRouter(_cfg(DTM_ENV="prod", DTM_LOCAL_MODEL="llama3.1"))
        provider, model = r.choose(allow_cloud=False)
        self.assertIsInstance(provider, OllamaProvider)
        self.assertTrue(provider.is_local)
        self.assertEqual(model, "llama3.1")

    def test_cloud_refused_when_global_flag_off(self):
        # task asks for cloud, key present, but DTM_ALLOW_CLOUD is off -> stays local
        r = ModelRouter(_cfg(DTM_ALLOW_CLOUD="0", ANTHROPIC_API_KEY="sk-xxx"))
        provider, _ = r.choose(allow_cloud=True)
        self.assertIsInstance(provider, OllamaProvider)

    def test_cloud_refused_without_key(self):
        r = ModelRouter(_cfg(DTM_ALLOW_CLOUD="1"))
        provider, _ = r.choose(allow_cloud=True)
        self.assertIsInstance(provider, OllamaProvider)

    def test_cloud_selected_when_all_conditions_met(self):
        r = ModelRouter(_cfg(DTM_ENV="prod", DTM_ALLOW_CLOUD="1", ANTHROPIC_API_KEY="sk-xxx"))
        provider, model = r.choose(allow_cloud=True)
        self.assertIsInstance(provider, ClaudeProvider)
        self.assertFalse(provider.is_local)

    def test_mock_round_trips(self):
        r = ModelRouter(_cfg())
        res = r.mock().chat([{"role": "user", "content": "ping"}], [], "m")
        self.assertIn("ping", res.content)


if __name__ == "__main__":
    unittest.main()
