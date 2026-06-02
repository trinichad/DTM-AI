"""Config tests — fingerprinting, fail-closed require, and 0600 enforcement (I-3)."""
import os
import tempfile
import unittest
from pathlib import Path

from execution.core.config import Config, ConfigError, fingerprint


class ConfigSecurity(unittest.TestCase):
    def _env(self, body: str, mode: int = 0o600) -> Path:
        d = tempfile.mkdtemp()
        p = Path(d) / ".env"
        p.write_text(body, encoding="utf-8")
        p.chmod(mode)
        return p

    def test_fingerprint_never_reveals_secret(self):
        fp = fingerprint("super-secret-value")
        self.assertEqual(len(fp), 7)
        self.assertNotIn("secret", fp)
        self.assertEqual(fingerprint(""), "—")

    def test_require_fails_closed(self):
        cfg = Config(env_path=self._env("PRESENT=yes\n"))
        self.assertEqual(cfg.require("PRESENT"), "yes")
        with self.assertRaises(ConfigError):
            cfg.require("ABSENT")

    def test_world_readable_env_refused(self):
        if os.name != "posix":
            self.skipTest("posix-only permission check")
        p = self._env("X=1\n", mode=0o644)
        with self.assertRaises(ConfigError):
            Config(env_path=p)

    def test_process_env_wins_over_file(self):
        p = self._env("OVERRIDE_ME=file\n")
        os.environ["OVERRIDE_ME"] = "env"
        try:
            self.assertEqual(Config(env_path=p).get("OVERRIDE_ME"), "env")
        finally:
            del os.environ["OVERRIDE_ME"]

    def test_bool_parsing(self):
        cfg = Config(env_path=self._env("A=1\nB=false\nC=on\n"))
        self.assertTrue(cfg.bool("A"))
        self.assertFalse(cfg.bool("B"))
        self.assertTrue(cfg.bool("C"))


if __name__ == "__main__":
    unittest.main()
