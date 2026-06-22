"""Write-action approval workflow — propose -> human approve -> execute (args-bound, one-shot)."""
import os
import tempfile
import unittest
from pathlib import Path

from execution.core.context import ToolContext
from execution.core.dispatch import dispatch
from execution.core.memory import VaultStore
from execution.runtime import build_agent
from execution.web.api import Api
from execution.web.auth import AuthStore, SessionSigner


class ApprovalFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "a.db"
        os.environ["MSPAI_VAULT_PATH"] = str(Path(self.tmp.name) / "vault")
        self.agent = build_agent(db_path=self.db)
        # memory_note is now floored to auto-run (D-47), so it can't exercise the approval flow.
        # Inject a NON-msp_ai write fixture that IS gated, and writes the vault observably.
        from execution.core.registry import _coerce
        from tests.fixture_skills import fx_client_write
        self.agent.registry._tools["fx_client_write"] = _coerce(fx_client_write)
        self.agent.caps.set("fx_client_write", allow_write=True, require_approval=True)
        self.auth = AuthStore(self.db)
        self.auth.ensure_admin("adminpass")
        self.auth.create_user("tech1", "techpass1", "user")
        self.api = Api(self.agent, self.auth, SessionSigner(secret=b"0" * 32))
        self.ctx = ToolContext(tenant_id="acme", actor="hermes")

    def tearDown(self):
        os.environ.pop("MSPAI_VAULT_PATH", None)
        self.auth.close()
        self.tmp.cleanup()

    def _write(self, note="Sunday maintenance"):
        return dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                        name="fx_client_write", args={"note": note},
                        gate=self.agent.gate, approvals=self.agent.approvals)

    def test_write_creates_pending_and_does_not_execute(self):
        env = self._write()
        self.assertFalse(env["ok"])
        self.assertEqual(env["status"], "pending_approval")
        self.assertIsNotNone(env["approval_id"])
        # nothing written to the vault yet
        self.assertEqual(VaultStore().read_memory("acme"), "")
        self.assertEqual(self.agent.approvals.count_pending(), 1)

    def test_describe_approval_preview_is_resolved_and_stored(self):
        # D-90: a write tool's describe_approval(ctx,args) resolves a human-readable preview that
        # dispatch stamps on the approval row + the pending envelope, so the card shows WHAT will
        # change (here a plain dict), not just raw args.
        env = self._write("Sunday patch")
        self.assertEqual(env["approval_preview"], {"Note": "Sunday patch", "Client": "acme"})
        row = self.agent.approvals.get(env["approval_id"])
        self.assertEqual(row["args_preview"], {"Note": "Sunday patch", "Client": "acme"})

    def test_pending_turn_carries_the_preview_to_the_card(self):
        provider = self.agent.router.mock([
            {"content": "", "tool_calls": [{"name": "fx_client_write",
                                            "arguments": {"note": "preview me"}}]},
            {"content": "x"}])
        turn = self.agent.chat(self.ctx, "do it", provider=provider)
        self.assertEqual(turn.pending["preview"], {"Note": "preview me", "Client": "acme"})

    def test_preview_failure_falls_back_to_raw_args(self, ):
        # A describe_approval that raises must never block the approval — preview is just None.
        import tests.fixture_skills.fx_client_write as fx
        orig = fx.describe_approval
        fx.describe_approval = lambda ctx, args: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            from execution.core.registry import _coerce
            self.agent.registry._tools["fx_client_write"] = _coerce(fx)
            env = self._write("still works")
            self.assertEqual(env["status"], "pending_approval")
            self.assertIsNone(env["approval_preview"])
        finally:
            fx.describe_approval = orig
            self.agent.registry._tools["fx_client_write"] = _coerce(fx)

    def test_approve_executes_with_exact_args(self):
        aid = self._write("VPN renewal in August")["approval_id"]
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin")
        self.assertEqual(r.status, 200)
        self.assertTrue(r.payload["executed"])
        # the exact proposed note is now in the vault
        self.assertIn("VPN renewal in August", VaultStore().read_memory("acme"))
        self.assertEqual(self.agent.approvals.get(aid)["status"], "executed")

    def test_reject_does_not_execute(self):
        aid = self._write("should not happen")["approval_id"]
        r = self.api.handle("POST", f"/api/approvals/{aid}/reject", {}, {}, "admin")
        self.assertEqual(r.status, 200)
        self.assertEqual(VaultStore().read_memory("acme"), "")
        self.assertEqual(self.agent.approvals.get(aid)["status"], "rejected")

    def test_one_shot(self):
        aid = self._write()["approval_id"]
        self.assertEqual(self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin").status, 200)
        # second approve must fail (already decided)
        self.assertEqual(self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin").status, 409)

    def test_non_admin_cannot_approve(self):
        aid = self._write()["approval_id"]
        self.assertEqual(self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "tech1").status, 403)
        self.assertEqual(self.agent.approvals.get(aid)["status"], "pending")  # untouched

    def test_trusted_write_skips_approval(self):
        # with the per-tool Approval toggle OFF (require_approval False), the write auto-runs inline
        self.agent.caps.set("fx_client_write", allow_write=True, require_approval=False)
        env = self._write("trusted note")
        self.assertTrue(env["ok"])
        self.assertEqual(self.agent.approvals.count_pending(), 0)
        self.assertIn("trusted note", VaultStore().read_memory("acme"))

    def test_chat_pauses_on_approval_needed_write(self):
        # D-47: the streaming turn PAUSES (turn.pending) instead of narrating a failure; the tool
        # does not run, and an approval is queued.
        provider = self.agent.router.mock([
            {"content": "", "tool_calls": [{"name": "fx_client_write",
                                            "arguments": {"note": "paused note"}}]},
            {"content": "should not reach"}])
        events = []
        turn = self.agent.chat_stream(self.ctx, "do the write", lambda e: events.append(e),
                                      provider=provider)
        self.assertIsNotNone(turn.pending)
        self.assertEqual(turn.pending["tool"], "fx_client_write")
        self.assertEqual(self.agent.approvals.count_pending(), 1)
        self.assertTrue(any(e.get("type") == "approval_required" for e in events))
        self.assertEqual(VaultStore().read_memory("acme"), "")     # nothing ran

    def test_nonstreaming_chat_pauses_on_approval_needed_write(self):
        # Regression: the non-streaming chat() (used by the approval CONTINUATION, Teams, and
        # delegation) must PAUSE on a pending-approval write exactly like chat_stream — otherwise
        # it feeds "approval required" back to the model and silently fires the NEXT writes,
        # piling up orphan approvals that only surface in the bell instead of one inline card.
        provider = self.agent.router.mock([
            {"content": "", "tool_calls": [{"name": "fx_client_write",
                                            "arguments": {"note": "first"}}]},
            {"content": "", "tool_calls": [{"name": "fx_client_write",
                                            "arguments": {"note": "second — must not be reached"}}]},
            {"content": "should not reach"}])
        turn = self.agent.chat(self.ctx, "do two writes", provider=provider)
        self.assertIsNotNone(turn.pending)
        self.assertEqual(turn.pending["tool"], "fx_client_write")
        self.assertEqual(turn.pending["args"], {"note": "first"})
        # Paused at the FIRST write: exactly one approval queued, second never proposed.
        self.assertEqual(self.agent.approvals.count_pending(), 1)
        self.assertEqual(VaultStore().read_memory("acme"), "")     # nothing ran

    def test_approve_from_chat_posts_result_and_clears_buttons(self):
        # D-47: approving the inline button executes AND posts the outcome back into the chat, and
        # the paused message's buttons are cleared so they don't reappear.
        aid = self._write("chat-approved note")["approval_id"]
        convs = self.agent.conversations
        cid = convs.create("admin", tenant_id="acme")["id"]
        convs.add_message("admin", cid, "assistant", "needs approval",
                          meta={"pending": {"id": aid, "tool": "fx_client_write"}})
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {},
                            {"conversation_id": cid}, "admin")
        self.assertTrue(r.payload["executed"])
        self.assertIn("Approved", r.payload["message"])
        msgs = convs.get("admin", cid)["messages"]
        self.assertIsNone(msgs[0]["meta"].get("pending"))               # buttons cleared
        self.assertEqual(msgs[0]["meta"].get("pending_resolved"), "executed")
        self.assertTrue(any(m["role"] == "assistant" and "Approved" in m["content"]
                            for m in msgs[1:]))                         # result posted to chat
        self.assertIn("chat-approved note", VaultStore().read_memory("acme"))

    def test_set_tenant_rebinds_conversation(self):
        # D-52: an all-clients chat can be locked onto one client; owner-checked.
        convs = self.agent.conversations
        cid = convs.create("admin", tenant_id="*")["id"]
        self.assertEqual(convs.tenant_of("admin", cid), "*")
        self.assertTrue(convs.set_tenant("admin", cid, "acme"))
        self.assertEqual(convs.tenant_of("admin", cid), "acme")
        self.assertFalse(convs.set_tenant("bob", cid, "x"))        # not bob's conversation

    def test_focus_client_locks_the_turn_to_one_client(self):
        # D-52: in '*', a focus_client call narrows the turn + flags it so the chat re-binds.
        import os
        from pathlib import Path
        (Path(os.environ["MSPAI_VAULT_PATH"]) / "clients" / "acme").mkdir(parents=True, exist_ok=True)
        provider = self.agent.router.mock([
            {"content": "", "tool_calls": [{"name": "focus_client", "arguments": {"client": "acme"}}]},
            {"content": "Locked to acme — here's what you asked."}])
        ctx = ToolContext(tenant_id="*", actor="admin")
        events = []
        turn = self.agent.chat_stream(ctx, "show me acme users", lambda e: events.append(e),
                                      provider=provider)
        self.assertEqual(turn.focus_client, "acme")
        self.assertEqual(ctx.tenant_id, "acme")                    # narrowed for the rest of the turn
        self.assertTrue(any(e.get("type") == "client_locked" and e.get("tenant") == "acme"
                            for e in events))

    def test_focus_client_rejects_unknown_client(self):
        from execution.core.dispatch import dispatch
        ctx = ToolContext(tenant_id="*", actor="admin")
        env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx,
                       name="focus_client", args={"client": "nope-co"},
                       gate=self.agent.gate, approvals=self.agent.approvals)
        self.assertFalse(env["ok"])
        self.assertIn("no registered client", env["error"])

    def test_own_vault_write_auto_runs_without_approval(self):
        # D-47: a msp_ai own-vault write (memory_note) is floored to never need approval, even if a
        # stale capability row says otherwise.
        self.agent.caps.set("memory_note", allow_write=False, require_approval=True)
        env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                       name="memory_note", args={"note": "auto note"},
                       gate=self.agent.gate, approvals=self.agent.approvals)
        self.assertTrue(env["ok"], env)
        self.assertEqual(self.agent.approvals.count_pending(), 0)
        self.assertIn("auto note", VaultStore().read_memory("acme"))


class ApprovalContinuation(unittest.TestCase):
    """D-62 — after an inline approval executes, the agent RESUMES the task on the same model."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "a.db"
        os.environ["MSPAI_VAULT_PATH"] = str(Path(self.tmp.name) / "vault")
        self.agent = build_agent(db_path=self.db)
        from execution.core.registry import _coerce
        from tests.fixture_skills import fx_client_write
        self.agent.registry._tools["fx_client_write"] = _coerce(fx_client_write)
        self.agent.caps.set("fx_client_write", allow_write=True, require_approval=True)
        self.auth = AuthStore(self.db)
        self.auth.ensure_admin("adminpass")
        self.api = Api(self.agent, self.auth, SessionSigner(secret=b"0" * 32))
        self.ctx = ToolContext(tenant_id="acme", actor="hermes")

    def tearDown(self):
        os.environ.pop("MSPAI_VAULT_PATH", None)
        self.auth.close()
        self.tmp.cleanup()

    def test_approve_runs_a_continuation_turn_on_the_conversations_model(self):
        from execution.agent import AgentTurn
        convs = self.agent.conversations
        conv_id = convs.create("admin", tenant_id="acme")["id"]
        convs.add_message("admin", conv_id, "user", "hide fred from the address book")
        aid = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                       name="fx_client_write", args={"note": "hide fred"},
                       gate=self.agent.gate, approvals=self.agent.approvals)["approval_id"]
        convs.add_message("admin", conv_id, "assistant", "needs approval",
                          meta={"pending": {"id": aid, "tool": "fx_client_write"},
                                "label": "openai-codex/gpt-5.5 · 2 round(s)"})
        seen = {}
        def fake_chat(ctx, message, model_id=None, history=None, **kw):
            seen.update({"message": message, "model_id": model_id,
                         "history_len": len(history or [])})
            return AgentTurn(answer="Done — verified Fred is hidden.",
                             provider="openai-codex", model="gpt-5.5", rounds=1)
        self.agent.chat = fake_chat
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {},
                            {"conversation_id": conv_id}, "admin")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.payload["continuation"]["message"],
                         "Done — verified Fred is hidden.")
        # same model as the conversation, not the local default
        self.assertEqual(seen["model_id"], "openai-codex:gpt-5.5")
        # the synthetic instruction carries the executed result and is NOT a stored user msg, and
        # forces ACTION on remaining steps rather than narration (D-92)
        self.assertIn("APPROVED", seen["message"])
        self.assertIn("ALREADY RAN", seen["message"])
        self.assertIn("CALL the necessary tool", seen["message"])
        msgs = convs.get("admin", conv_id)["messages"]
        self.assertEqual([m["role"] for m in msgs],
                         ["user", "assistant", "assistant", "assistant"])  # no extra user msg
        self.assertEqual(msgs[-1]["content"], "Done — verified Fred is hidden.")

    def test_continuation_failure_never_masks_the_executed_action(self):
        convs = self.agent.conversations
        conv_id = convs.create("admin", tenant_id="acme")["id"]
        convs.add_message("admin", conv_id, "user", "do the thing")
        aid = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                       name="fx_client_write", args={"note": "x"},
                       gate=self.agent.gate, approvals=self.agent.approvals)["approval_id"]
        def boom(*a, **k):
            raise RuntimeError("model exploded")
        self.agent.chat = boom
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {},
                            {"conversation_id": conv_id}, "admin")
        self.assertEqual(r.status, 200)                    # approval itself succeeded
        self.assertTrue(r.payload["executed"])
        self.assertIsNone(r.payload["continuation"])
        self.assertIn("VPN" if False else "x", VaultStore().read_memory("acme"))  # action ran

    def test_dispatch_records_the_originating_conversation_on_the_approval(self):
        # The chat handler tags the ctx with its conversation_id; dispatch must persist it on the
        # approval row so ANY surface (inline OR bell) can later resume the right thread.
        ctx = ToolContext(tenant_id="acme", actor="admin (chat)",
                          _meta={"conversation_id": "conv-xyz"})
        aid = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx,
                       name="fx_client_write", args={"note": "n"},
                       gate=self.agent.gate, approvals=self.agent.approvals)["approval_id"]
        self.assertEqual(self.agent.approvals.get(aid)["conversation_id"], "conv-xyz")

    def test_bell_approval_resumes_via_stored_conversation(self):
        # The bell sends NO conversation_id; the continuation must still fire, driven by the
        # conversation_id stored on the approval row (D-62 follow-up).
        from execution.agent import AgentTurn
        convs = self.agent.conversations
        conv_id = convs.create("admin", tenant_id="acme")["id"]
        convs.add_message("admin", conv_id, "user", "do the write")
        ctx = ToolContext(tenant_id="acme", actor="admin (chat)",
                          _meta={"conversation_id": conv_id})
        aid = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx,
                       name="fx_client_write", args={"note": "bell note"},
                       gate=self.agent.gate, approvals=self.agent.approvals)["approval_id"]
        convs.add_message("admin", conv_id, "assistant", "needs approval",
                          meta={"pending": {"id": aid, "tool": "fx_client_write"}})
        self.agent.chat = lambda *a, **k: AgentTurn(answer="Resumed and finished.",
                                                    provider="mock", model="m", rounds=1)
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin")  # no conv_id
        self.assertTrue(r.payload["executed"])
        self.assertEqual(r.payload["continuation"]["message"], "Resumed and finished.")
        msgs = convs.get("admin", conv_id)["messages"]
        self.assertIsNone(msgs[1]["meta"].get("pending"))                 # buttons cleared
        self.assertEqual(msgs[-1]["content"], "Resumed and finished.")    # continuation posted

    def test_stream_approval_executes_then_streams_continuation(self):
        # The inline streaming path: a `decided` frame (executed + summary), then live continuation
        # frames, then a final `answer` frame — all without a synchronous block on the POST.
        convs = self.agent.conversations
        conv_id = convs.create("admin", tenant_id="acme")["id"]
        convs.add_message("admin", conv_id, "user", "do the write")
        aid = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                       name="fx_client_write", args={"note": "streamed"},
                       gate=self.agent.gate, approvals=self.agent.approvals)["approval_id"]
        frames = list(self.api.stream_approval(
            {"approval_id": aid, "conversation_id": conv_id}, "admin"))
        kinds = [f["type"] for f in frames]
        self.assertEqual(kinds[0], "decided")
        self.assertTrue(frames[0]["executed"])
        self.assertEqual(kinds[-1], "answer")                  # continuation streamed to an answer
        self.assertIn("streamed", VaultStore().read_memory("acme"))   # the action actually ran

    def test_stream_approval_rejects_double_decision(self):
        aid = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                       name="fx_client_write", args={"note": "once"},
                       gate=self.agent.gate, approvals=self.agent.approvals)["approval_id"]
        self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin")
        frames = list(self.api.stream_approval({"approval_id": aid}, "admin"))
        self.assertEqual(frames[0]["type"], "error")          # one-shot guard holds


class ConnectorSelfExtension(unittest.TestCase):
    """D-64 — propose a cmdlet → owner approves → grant is live, end to end through dispatch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "a.db"
        os.environ["MSPAI_VAULT_PATH"] = str(Path(self.tmp.name) / "vault")
        self.agent = build_agent(db_path=self.db)
        self.agent.audit.set_enabled("propose_connector_capability", True)
        self.agent.caps.set("propose_connector_capability", allow_write=True,
                            require_approval=True)
        self.auth = AuthStore(self.db)
        self.auth.ensure_admin("adminpass")
        self.api = Api(self.agent, self.auth, SessionSigner(secret=b"0" * 32))
        self.ctx = ToolContext(tenant_id="acme", actor="hermes")

    def tearDown(self):
        os.environ.pop("MSPAI_VAULT_PATH", None)
        self.auth.close()
        self.tmp.cleanup()

    def test_propose_pauses_then_approve_makes_the_cmdlet_live(self):
        from execution.core import connector_grants
        # the agent proposes a new cmdlet — dispatch must PAUSE it (nothing granted yet)
        env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                       name="propose_connector_capability",
                       args={"connector": "exo", "cmdlet": "Set-CASMailbox", "kind": "write",
                             "params": ["Identity", "OWAEnabled"], "reason": "OWA"},
                       gate=self.agent.gate, approvals=self.agent.approvals)
        self.assertEqual(env["status"], "pending_approval")
        self.assertEqual(connector_grants.grants_for("exo"), ({}, {}))   # not yet
        aid = env["approval_id"]
        # owner approves → the grant becomes live
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin")
        self.assertEqual(r.status, 200)
        self.assertTrue(r.payload["executed"])
        self.assertEqual(connector_grants.grants_for("exo")[0]["Set-CASMailbox"], "write")
        # …and it shows up (revocable) in the approvals view
        listing = self.api.handle("GET", "/api/approvals", {}, {}, "admin")
        cmds = [g["cmdlet"] for g in listing.payload["connector_grants"]]
        self.assertIn("Set-CASMailbox", cmds)
        rev = self.api.handle("POST", "/api/connector-grants/revoke", {},
                              {"connector": "exo", "cmdlet": "Set-CASMailbox"}, "admin")
        self.assertTrue(rev.payload["revoked"])
        self.assertEqual(connector_grants.grants_for("exo"), ({}, {}))

    def test_proposing_a_destructive_cmdlet_is_refused_at_run(self):
        # approval can pause it, but execution refuses — the floor holds at run()
        from execution.core import connector_grants
        env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                       name="propose_connector_capability",
                       args={"connector": "exo", "cmdlet": "Remove-Mailbox", "kind": "write"},
                       gate=self.agent.gate, approvals=self.agent.approvals)
        aid = env["approval_id"]
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin")
        self.assertFalse(r.payload["executed"])          # run() returned an error
        self.assertEqual(connector_grants.grants_for("exo"), ({}, {}))


class BatchApprovals(unittest.TestCase):
    """D-59 — approve once, auto-approve the repeats: bounded grant (tenant+tool, count, TTL),
    destructive floor intact, revocable, only armed when the first run succeeded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "a.db"
        os.environ["MSPAI_VAULT_PATH"] = str(Path(self.tmp.name) / "vault")
        self.agent = build_agent(db_path=self.db)
        from execution.core.registry import _coerce
        from tests.fixture_skills import fx_client_write
        self.agent.registry._tools["fx_client_write"] = _coerce(fx_client_write)
        self.agent.caps.set("fx_client_write", allow_write=True, require_approval=True)
        self.auth = AuthStore(self.db)
        self.auth.ensure_admin("adminpass")
        self.auth.create_user("tech1", "techpass1", "user")
        self.api = Api(self.agent, self.auth, SessionSigner(secret=b"0" * 32))
        self.ctx = ToolContext(tenant_id="acme", actor="hermes")

    def tearDown(self):
        os.environ.pop("MSPAI_VAULT_PATH", None)
        self.auth.close()
        self.tmp.cleanup()

    def _write(self, note, tenant="acme"):
        ctx = self.ctx if tenant == "acme" else ToolContext(tenant_id=tenant, actor="hermes")
        return dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx,
                        name="fx_client_write", args={"note": note},
                        gate=self.agent.gate, approvals=self.agent.approvals)

    def test_batch_approve_arms_grant_and_auto_runs_repeats(self):
        aid = self._write("user 1")["approval_id"]
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {"batch": True},
                            "admin")
        self.assertEqual(r.status, 200)
        self.assertTrue(r.payload["executed"])
        self.assertIn("Auto-approval armed", r.payload["message"])
        grants = self.agent.gate.list_batches()
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]["remaining"], 25)
        # the next identical call runs WITHOUT a new approval — and says so
        env = self._write("user 2")
        self.assertTrue(env["ok"], env)
        self.assertIn("auto-approved by batch grant", env["auto_approved"])
        self.assertEqual(self.agent.approvals.count_pending(), 0)
        self.assertIn("user 2", VaultStore().read_memory("acme"))
        # …but a DIFFERENT client is not covered (grant is tenant-scoped)
        env2 = self._write("other client", tenant="globex")
        self.assertEqual(env2.get("status"), "pending_approval")

    def test_grant_counts_down_then_normal_approvals_resume(self):
        self.agent.gate.grant_batch("acme", "fx_client_write", count=2, approval_id=7)
        self.assertTrue(self._write("a")["ok"])
        self.assertTrue(self._write("b")["ok"])
        env = self._write("c")                                  # grant exhausted
        self.assertEqual(env.get("status"), "pending_approval")

    def test_grant_expires(self):
        import time as _t
        self.agent.gate.grant_batch("acme", "fx_client_write", count=10, approval_id=7)
        self.agent.gate._batch[("acme", "fx_client_write")]["expires_at"] = _t.time() - 1
        env = self._write("late")
        self.assertEqual(env.get("status"), "pending_approval")
        self.assertEqual(self.agent.gate.list_batches(), [])    # swept

    def test_destructive_can_never_be_batch_granted(self):
        from execution.core.registry import _coerce
        from tests.fixture_skills import fx_destructive
        self.agent.registry._tools["fx_destructive"] = _coerce(fx_destructive)
        self.agent.caps.set("fx_destructive", allow_write=True, require_approval=True)
        # refused at grant time…
        self.assertIsNone(self.agent.gate.grant_batch("acme", "fx_destructive", count=5))
        # …and even a grant forced into the map is refused at consume time (double floor)
        self.agent.gate._batch[("acme", "fx_destructive")] = {
            "tenant_id": "acme", "tool": "fx_destructive", "granted": 5, "remaining": 5,
            "expires_at": 9e12, "approval_id": 1, "by": "x"}
        env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                       name="fx_destructive", args={}, gate=self.agent.gate,
                       approvals=self.agent.approvals)
        self.assertEqual(env.get("status"), "pending_approval")

    def test_failed_first_run_does_not_arm(self):
        import types
        from execution.core.registry import _coerce
        failing = types.SimpleNamespace(
            NAME="fx_failing_write", DESCRIPTION="always fails", SOURCE="fixture",
            CATEGORY="write", RISK_LEVEL="high", REQUIRES_APPROVAL=True,
            ENABLED_BY_DEFAULT=True,
            PARAMETERS={"type": "object", "properties": {}, "additionalProperties": False},
            run=lambda ctx, **k: {"error": "vendor said no"}, __name__="fx_failing_write")
        self.agent.registry._tools["fx_failing_write"] = _coerce(failing)
        self.agent.caps.set("fx_failing_write", allow_write=True, require_approval=True)
        aid = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                       name="fx_failing_write", args={}, gate=self.agent.gate,
                       approvals=self.agent.approvals)["approval_id"]
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {"batch": True},
                            "admin")
        self.assertEqual(r.status, 200)
        self.assertFalse(r.payload["executed"])
        self.assertIn("No auto-approval armed", r.payload["message"])
        self.assertEqual(self.agent.gate.list_batches(), [])

    def test_revoke_endpoint_and_listing(self):
        self.agent.gate.grant_batch("acme", "fx_client_write", count=10, approval_id=3)
        listed = self.api.handle("GET", "/api/approvals", {}, {}, "admin")
        self.assertEqual(len(listed.payload["batch_grants"]), 1)
        # non-admin cannot revoke
        self.assertEqual(self.api.handle("POST", "/api/approvals/batch/revoke", {}, {},
                                         "tech1").status, 403)
        r = self.api.handle("POST", "/api/approvals/batch/revoke", {}, {}, "admin")
        self.assertEqual(r.payload["revoked"], 1)
        self.assertEqual(self._write("after revoke").get("status"), "pending_approval")


if __name__ == "__main__":
    unittest.main()
