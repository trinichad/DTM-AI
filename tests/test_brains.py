"""Per-agent brain tests — sidecar set/get/clear, catalog validation, and the Dispatcher
running a specialist on its pinned brain (cloud brain → allow_cloud for that run only)."""
import tempfile
import types
import unittest
from pathlib import Path

from execution.core.agents import get_agent, get_brain_model, set_brain
from execution.core.context import ToolContext
from execution.core.router import ModelRouter
from execution.core.tasks import Dispatcher, TaskStore


class StubCfg:
    def __init__(self, d): self.d = d
    def get(self, k, default=None): return self.d.get(k, default)
    def int(self, k, default=0):
        try: return int(self.d.get(k, default))
        except (TypeError, ValueError): return default
    def present(self, k): return bool(self.d.get(k))


def _write(p: Path, t: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(t)


class Brains(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        _write(self.d / "SOUL.md", "# AtlasOps\n- name: AtlasOps\n- role: Director\n")
        _write(self.d / "config.yaml", "model:\n  default: gpt-5.5\n  provider: openai-codex\n")
        pp = self.d / "profiles" / "patchwright"
        _write(pp / "SOUL.md", "# PW\n- name: Patchwright\n- role: Kaseya\n")
        _write(pp / "config.yaml", "model:\n  default: qwen3.5:27b\n  provider: custom\n")
        _write(pp / "profile.yaml", "description: 'Kaseya ops'\n")
        self.cfg = StubCfg({"MSPAI_AGENTS_DIR": str(self.d)})

    def tearDown(self):
        self.tmp.cleanup()

    # ── sidecar set / get / clear ──
    def test_set_and_get_cloud_brain(self):
        set_brain("patchwright", "anthropic:claude-opus-4-8", self.cfg)
        self.assertEqual(get_brain_model("patchwright", self.cfg), "anthropic:claude-opus-4-8")
        b = get_agent("patchwright", self.cfg)["brain"]
        self.assertEqual(b["mode"], "cloud")
        self.assertEqual(b["model"], "claude-opus-4-8")
        self.assertEqual(b["model_id"], "anthropic:claude-opus-4-8")

    def test_local_brain_mode(self):
        set_brain("patchwright", "ollama:qwen3.5:27b", self.cfg)
        self.assertEqual(get_agent("patchwright", self.cfg)["brain"]["mode"], "local")

    def test_clear_falls_back_to_config(self):
        set_brain("patchwright", "anthropic:claude-opus-4-8", self.cfg)
        set_brain("patchwright", "", self.cfg)                      # clear
        self.assertIsNone(get_brain_model("patchwright", self.cfg))
        # display falls back to the legacy config.yaml (provider custom → local)
        self.assertEqual(get_agent("patchwright", self.cfg)["brain"]["mode"], "local")

    def test_unknown_agent_raises(self):
        with self.assertRaises(FileNotFoundError):
            set_brain("ghost", "ollama:x", self.cfg)

    # ── catalog (full list, valid even before the key is set) ──
    def test_catalog_marks_unkeyed_cloud_unavailable(self):
        r = ModelRouter(StubCfg({"MSPAI_LOCAL_MODEL": "qwen3.5:27b"}))   # no ANTHROPIC_API_KEY
        cat = {m["id"]: m for m in r.catalog_models()}
        self.assertTrue(cat["ollama:qwen3.5:27b"]["available"])        # local always
        self.assertIn("anthropic:claude-opus-4-8", cat)                # present in catalog
        self.assertFalse(cat["anthropic:claude-opus-4-8"]["available"])  # but not runnable (no key)
        self.assertTrue(r.is_catalog_model("anthropic:claude-opus-4-8"))
        self.assertFalse(r.is_catalog_model("anthropic:not-a-model"))

    def test_catalog_available_when_keyed(self):
        r = ModelRouter(StubCfg({"ANTHROPIC_API_KEY": "sk-x"}))
        cat = {m["id"]: m for m in r.catalog_models()}
        self.assertTrue(cat["anthropic:claude-opus-4-8"]["available"])

    # ── Dispatcher runs the specialist on its brain ──
    def test_dispatcher_uses_cloud_brain_and_allows_cloud(self):
        store = TaskStore(self.d / "t.db")
        self.addCleanup(store.close)
        store.create("check drift", assignee="patchwright", tenant="acme")
        calls = []

        class FakeAgent:
            def chat(self, ctx, message, *, profile=None, model_id=None, **kw):
                calls.append({"profile": profile, "model_id": model_id, "allow_cloud": ctx.allow_cloud})
                return types.SimpleNamespace(answer="ok", citations=[], tool_events=[],
                                             provider="claude", model="m", rounds=1)

        disp = Dispatcher(store, FakeAgent(),
                          lambda tenant, actor: ToolContext(tenant_id=tenant, actor=actor),
                          model_resolver=lambda p: get_brain_model(p, self.cfg))
        set_brain("patchwright", "anthropic:claude-opus-4-8", self.cfg)
        disp._run_one(store.claim_next_ready())
        self.assertEqual(calls[0]["model_id"], "anthropic:claude-opus-4-8")
        self.assertTrue(calls[0]["allow_cloud"])                    # cloud brain opted this run in

    def test_dispatcher_local_brain_no_cloud(self):
        store = TaskStore(self.d / "t2.db")
        self.addCleanup(store.close)
        store.create("inventory", assignee="patchwright", tenant="acme")
        calls = []

        class FakeAgent:
            def chat(self, ctx, message, *, profile=None, model_id=None, **kw):
                calls.append({"model_id": model_id, "allow_cloud": ctx.allow_cloud})
                return types.SimpleNamespace(answer="ok", citations=[], tool_events=[],
                                             provider="ollama", model="m", rounds=1)

        disp = Dispatcher(store, FakeAgent(),
                          lambda tenant, actor: ToolContext(tenant_id=tenant, actor=actor),
                          model_resolver=lambda p: get_brain_model(p, self.cfg))
        set_brain("patchwright", "ollama:qwen3.5:27b", self.cfg)
        disp._run_one(store.claim_next_ready())
        self.assertEqual(calls[0]["model_id"], "ollama:qwen3.5:27b")
        self.assertFalse(calls[0]["allow_cloud"])                   # local brain stays local


if __name__ == "__main__":
    unittest.main()
