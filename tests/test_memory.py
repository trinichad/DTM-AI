"""Memory + KB tests — vault search/read/write, path-safety, and the skills via dispatch."""
import tempfile
import unittest
from pathlib import Path

from execution.core.audit import AuditStore
from execution.core.capabilities import CapabilityStore
from execution.core.context import ToolContext
from execution.core.dispatch import dispatch
from execution.core.gates import ConfigurableApprovalGate
from execution.core.memory import VaultStore, _safe_tenant
from execution.core.registry import Registry


class Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = VaultStore(path=Path(self.tmp.name))
        (self.v.kb_dir / "net").mkdir(parents=True)
        (self.v.kb_dir / "net" / "firewall.md").write_text(
            "# SonicWall\nTo reset the admin password, hold reset 15s then use mgmt port.", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_kb_search_finds_and_ranks(self):
        hits = self.v.search_kb("reset admin password")
        self.assertTrue(hits)
        self.assertIn("firewall.md", hits[0]["doc"])
        self.assertIn("reset", hits[0]["snippet"].lower())

    def test_kb_search_requires_all_terms(self):
        self.assertEqual(self.v.search_kb("reset zebrafish"), [])

    def test_kb_search_includes_bundled_reference(self):
        # the repo-bundled reference/ ships with the app and is searchable alongside the vault kb/
        hits = self.v.search_kb("kaseya executePowershell command")
        self.assertTrue(any("reference/" in h["doc"] for h in hits),
                        "bundled Kaseya command reference should be searchable via kb_search")

    def test_memory_roundtrip(self):
        self.assertEqual(self.v.read_memory("acme"), "")
        res = self.v.append_memory("acme", "prefers maintenance windows on Sundays", "tech1")
        self.assertTrue(res["ok"])
        text = self.v.read_memory("acme")
        self.assertIn("Sundays", text)
        self.assertIn("tech1", text)

    def test_memory_rejects_wildcard_tenant(self):
        self.assertIn("error", self.v.append_memory("*", "x", "t"))

    def test_path_traversal_sanitized(self):
        self.assertNotIn("/", _safe_tenant("../../etc/passwd"))
        self.assertNotIn("..", _safe_tenant("..%2f.."))


class MemorySkills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "m.db"
        self.audit = AuditStore(db)
        self.caps = CapabilityStore(db)
        self.reg = Registry()
        # seed internal-write policy as runtime.build_agent does
        for t in self.reg.all():
            if t.source == "dtm_ai" and t.is_write:
                self.caps.set(t.name, allow_write=True, require_approval=t.requires_approval)
        self.gate = ConfigurableApprovalGate(self.caps, self.reg)
        import os
        os.environ["DTM_VAULT_PATH"] = str(Path(self.tmp.name) / "vault")
        self.ctx = ToolContext(tenant_id="acme", actor="tech1")

    def tearDown(self):
        import os
        os.environ.pop("DTM_VAULT_PATH", None)
        self.audit.close()
        self.caps.close()
        self.tmp.cleanup()

    def _d(self, name, args=None):
        return dispatch(registry=self.reg, audit=self.audit, ctx=self.ctx, name=name,
                        args=args, gate=self.gate)

    def test_memory_note_then_read(self):
        w = self._d("memory_note", {"note": "VPN renewal due August"})
        self.assertTrue(w["ok"], w)
        r = self._d("memory_read")
        self.assertTrue(r["ok"])
        self.assertIn("VPN renewal", r["data"]["memory"])

    def test_memory_note_blocked_for_wildcard_tenant(self):
        ctx = ToolContext(tenant_id="*", actor="t")
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx, name="memory_note",
                       args={"note": "x"}, gate=self.gate)
        self.assertFalse(env["ok"])  # vault refuses '*' -> {"error":...} -> error envelope


if __name__ == "__main__":
    unittest.main()
