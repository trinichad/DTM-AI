"""AdminShell tests — command execution, cd persistence, per-user isolation, output cap, kill switch."""
import tempfile
import unittest
from pathlib import Path

from execution.core.adminshell import AdminShell, terminal_enabled
from execution.core.config import Config


class Shell(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        (Path(self.tmp.name) / "sub").mkdir()
        (Path(self.tmp.name) / "hello.txt").write_text("hi", encoding="utf-8")
        self.sh = AdminShell(base=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_runs_command_in_base(self):
        r = self.sh.run("admin", "ls")
        self.assertTrue(r["ok"])
        self.assertIn("hello.txt", r["stdout"])
        self.assertEqual(r["exit_code"], 0)

    def test_cd_persists_across_commands(self):
        r = self.sh.run("admin", "cd sub")
        self.assertTrue(r["ok"])
        self.assertTrue(r["cwd"].endswith("/sub"))
        r2 = self.sh.run("admin", "pwd")
        self.assertIn("/sub", r2["stdout"])               # new working dir carried over

    def test_cd_bad_dir_errors_and_keeps_cwd(self):
        r = self.sh.run("admin", "cd does-not-exist")
        self.assertFalse(r["ok"])
        self.assertIn("No such file", r["stderr"])
        self.assertEqual(self.sh.cwd("admin"), self.tmp.name)

    def test_per_user_isolation(self):
        self.sh.run("a", "cd sub")
        self.assertTrue(self.sh.cwd("a").endswith("/sub"))
        self.assertEqual(self.sh.cwd("b"), self.tmp.name)  # a's cd doesn't leak to b

    def test_nonzero_exit_reported(self):
        r = self.sh.run("admin", "false")
        self.assertFalse(r["ok"])
        self.assertEqual(r["exit_code"], 1)

    def test_output_is_capped(self):
        sh = AdminShell(base=self.tmp.name, max_output=1000)
        r = sh.run("admin", "yes x | head -c 50000")
        self.assertLessEqual(len(r["stdout"]), 1000)       # capped to configured max, not unbounded

    def test_no_timeout_by_default(self):
        self.assertIsNone(self.sh.timeout)                 # "no blocks" — no time limit unless configured

    def test_timeout_is_configurable(self):
        sh = AdminShell(base=self.tmp.name, timeout=1)
        r = sh.run("admin", "sleep 5")
        self.assertFalse(r["ok"])
        self.assertEqual(r["exit_code"], 124)              # killed at the configured limit

    def test_kill_switch(self):
        env = Path(self.tmp.name) / ".env"
        env.write_text("DTM_ENV=dev\nDTM_ADMIN_TERMINAL=0\n", encoding="utf-8"); env.chmod(0o600)
        self.assertFalse(terminal_enabled(Config(env_path=env)))
        env.write_text("DTM_ENV=dev\n", encoding="utf-8"); env.chmod(0o600)
        self.assertTrue(terminal_enabled(Config(env_path=env)))


if __name__ == "__main__":
    unittest.main()
