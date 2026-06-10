"""Web API tests — auth gating, capability edits, chat, all without sockets."""
import tempfile
import unittest
from pathlib import Path

from execution.runtime import build_agent
from execution.web.api import Api, Resp
from execution.web.auth import AuthStore, SessionSigner
from execution.web.server import _make_handler


class WebApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "w.db"
        self.agent = build_agent(db_path=db)
        self.auth = AuthStore(db)
        self.auth.ensure_admin("secret")
        self.signer = SessionSigner(secret=b"0" * 32)
        self.api = Api(self.agent, self.auth, self.signer)

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    def H(self, method, path, body=None, query=None, user=None):
        return self.api.handle(method, path, query or {}, body or {}, user)

    def test_auth_required(self):
        self.assertEqual(self.H("GET", "/api/tools").status, 401)

    def test_login_flow(self):
        self.assertEqual(self.H("POST", "/api/login", {"username": "admin", "password": "no"}).status, 401)
        r = self.H("POST", "/api/login", {"username": "admin", "password": "secret"})
        self.assertEqual(r.status, 200)
        self.assertIsNotNone(r.set_cookie)
        self.assertEqual(self.signer.verify(r.set_cookie), "admin")

    def test_tools_listed_with_policy(self):
        r = self.H("GET", "/api/tools", user="admin")
        names = {t["name"] for t in r.payload["tools"]}
        self.assertIn("system_health", names)
        self.assertIn("kaseya_list_assets", names)
        for t in r.payload["tools"]:
            self.assertIn("allow_write", t)

    def test_capability_edit_persists(self):
        self.H("POST", "/api/capabilities/echo_note", {"allow_write": True, "require_approval": False}, user="admin")
        tools = {t["name"]: t for t in self.H("GET", "/api/tools", user="admin").payload["tools"]}
        self.assertTrue(tools["echo_note"]["allow_write"])
        self.assertFalse(tools["echo_note"]["require_approval"])

    def test_capability_unknown_tool_404(self):
        self.assertEqual(self.H("POST", "/api/capabilities/nope", {"enabled": True}, user="admin").status, 404)

    def test_chat(self):
        r = self.H("POST", "/api/chat", {"tenant": "acme", "message": "status?"}, user="admin")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.payload["tenant"], "acme")
        self.assertIn("answer", r.payload)

    def test_chat_requires_message(self):
        self.assertEqual(self.H("POST", "/api/chat", {"tenant": "acme"}, user="admin").status, 400)

    def test_chat_persists_conversation(self):
        # a chat with no conversation_id creates one and persists both turns
        r = self.H("POST", "/api/chat", {"tenant": "acme", "message": "how many assets?"}, user="admin")
        cid = r.payload["conversation_id"]
        self.assertTrue(cid)
        self.assertEqual(r.payload["title"], "how many assets?")            # auto-titled
        convs = self.H("GET", "/api/conversations", user="admin").payload["conversations"]
        self.assertEqual(len(convs), 1)
        self.assertEqual(convs[0]["id"], cid)
        # continuing the SAME conversation keeps one conversation with the full transcript
        self.H("POST", "/api/chat", {"conversation_id": cid, "message": "and users?"}, user="admin")
        convs = self.H("GET", "/api/conversations", user="admin").payload["conversations"]
        self.assertEqual(len(convs), 1)
        msgs = self.H("GET", f"/api/conversations/{cid}", user="admin").payload["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user", "assistant"])

    def test_conversations_are_per_user(self):
        r = self.H("POST", "/api/chat", {"tenant": "acme", "message": "private"}, user="admin")
        cid = r.payload["conversation_id"]
        self.auth.create_user("bob", "bobpass1", "user")
        self.assertEqual(self.H("GET", "/api/conversations", user="bob").payload["conversations"], [])
        self.assertEqual(self.H("GET", f"/api/conversations/{cid}", user="bob").status, 404)
        self.assertEqual(self.H("DELETE", f"/api/conversations/{cid}", user="bob").status, 404)

    def test_do_delete_forwards_query_string(self):
        # Regression: do_DELETE dropped the query string, so DELETE /api/kb?doc=... arrived with
        # doc="" and the user couldn't delete their own KB doc. The verb must parse the query.
        captured = {}
        self.api.handle = lambda method, path, query, body, user: (
            captured.update(method=method, path=path, query=query, user=user) or Resp(200, {"ok": True}))
        Handler = _make_handler(self.api, self.signer, secure_cookie=False)
        h = Handler.__new__(Handler)                 # no socket — skip BaseHTTPRequestHandler.__init__
        h._user = lambda: "admin"
        h._send_json = lambda resp: None
        h.path = "/api/kb?doc=kb/test.md"
        h.do_DELETE()
        self.assertEqual(captured["path"], "/api/kb")
        self.assertEqual(captured["query"], {"doc": "kb/test.md"})   # query reached the router

    def test_terminal_admin_only_and_runs(self):
        # admin terminal (D-21): non-admins are blocked on both verbs
        self.auth.create_user("bob", "bobpass1", "user")
        self.assertEqual(self.H("GET", "/api/terminal", user="bob").status, 403)
        self.assertEqual(self.H("POST", "/api/terminal", {"command": "echo hi"}, user="bob").status, 403)
        # admin can run a command and see its output
        r = self.H("POST", "/api/terminal", {"command": "echo hello123"}, user="admin")
        self.assertEqual(r.status, 200)
        self.assertIn("hello123", r.payload["stdout"])
        # empty command rejected
        self.assertEqual(self.H("POST", "/api/terminal", {"command": "   "}, user="admin").status, 400)
        # GET state is admin-only and reports enabled + cwd
        st = self.H("GET", "/api/terminal", user="admin")
        self.assertEqual(st.status, 200)
        self.assertTrue(st.payload["enabled"])
        self.assertTrue(st.payload["cwd"])

    def test_conversation_rename_and_delete(self):
        cid = self.H("POST", "/api/conversations", {"tenant": "acme"}, user="admin").payload["id"]
        self.assertEqual(self.H("POST", f"/api/conversations/{cid}/rename",
                                {"title": "MFA audit"}, user="admin").status, 200)
        convs = self.H("GET", "/api/conversations", user="admin").payload["conversations"]
        self.assertEqual(convs[0]["title"], "MFA audit")
        self.assertEqual(self.H("DELETE", f"/api/conversations/{cid}", user="admin").status, 200)
        self.assertEqual(self.H("GET", "/api/conversations", user="admin").payload["conversations"], [])

    def test_stream_chat_emits_events_and_persists(self):
        events = list(self.api.stream_chat({"tenant": "acme", "message": "status?"}, "admin"))
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "start")              # first frame announces the conversation
        self.assertEqual(types[-1], "answer")            # last frame carries the canonical answer
        self.assertIn("delta", types)                    # streamed at least one token
        final = events[-1]
        cid = final["conversation_id"]
        self.assertTrue(cid)
        # the streamed turn was persisted just like the non-streaming path
        msgs = self.H("GET", f"/api/conversations/{cid}", user="admin").payload["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[1]["content"], final["answer"])

    def test_stream_chat_requires_message(self):
        events = list(self.api.stream_chat({"tenant": "acme"}, "admin"))
        self.assertEqual(events, [{"type": "error", "error": "message is required"}])

    def test_fleet_counts_structure(self):
        self.assertEqual(self.H("GET", "/api/fleet").status, 401)         # auth gated
        r = self.H("GET", "/api/fleet", user="admin")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.payload["tenant"], "*")
        names = {f["name"] for f in r.payload["fleet"]}
        self.assertIn("kaseya_list_assets", names)                        # fleet tool present
        for f in r.payload["fleet"]:
            self.assertIn("count", f)
            self.assertIn("ok", f)        # no creds in test -> ok False, count None (fail-closed, no network)

    def test_models_exposes_context_cap(self):
        r = self.H("GET", "/api/models", user="admin")
        self.assertIn("context", r.payload)
        self.assertGreater(r.payload["context"]["history_chars"], 0)

    def test_session_expiry(self):
        token = self.signer.make("admin", ttl_minutes=-1)  # already expired
        self.assertIsNone(self.signer.verify(token))


class ScheduledDelegation(unittest.TestCase):
    """Creating recurring board tasks through the manual Delegate form's API path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "w.db"
        self.agent = build_agent(db_path=db)
        self.auth = AuthStore(db)
        self.auth.ensure_admin("secret")
        self.api = Api(self.agent, self.auth, SessionSigner(secret=b"0" * 32))

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    def H(self, method, path, body=None, query=None, user=None):
        return self.api.handle(method, path, query or {}, body or {}, user)

    def test_create_scheduled_task(self):
        r = self.H("POST", "/api/kanban/tasks",
                   {"title": "drift check", "assignee": "patchwright",
                    "schedule": "daily 07:00", "tenant": "acme"}, user="admin")
        # profile may not exist on disk in this fixture — creation is store-level, so 200
        self.assertEqual(r.status, 200)
        self.assertEqual(r.payload["status"], "scheduled")
        self.assertTrue(r.payload["recurring"])
        self.assertEqual(r.payload["schedule_spec"], "daily 07:00")
        self.assertIsNotNone(r.payload["next_run_ms"])

    def test_bad_schedule_400(self):
        r = self.H("POST", "/api/kanban/tasks",
                   {"title": "x", "assignee": "pw", "schedule": "sometimes"}, user="admin")
        self.assertEqual(r.status, 400)
        self.assertIn("unrecognised schedule", r.payload["error"])

    def test_recurring_needs_assignee_400(self):
        r = self.H("POST", "/api/kanban/tasks", {"title": "x", "schedule": "hourly"}, user="admin")
        self.assertEqual(r.status, 400)
        self.assertIn("needs an assignee", r.payload["error"])

    def test_plain_task_unaffected(self):
        r = self.H("POST", "/api/kanban/tasks", {"title": "one-off"}, user="admin")
        self.assertEqual(r.status, 200)
        self.assertFalse(r.payload["recurring"])
        self.assertEqual(r.payload["status"], "triage")


if __name__ == "__main__":
    unittest.main()


class OwnerToolAuthoring(unittest.TestCase):
    """D-23 — admin add/edit/rename/delete of live skills, validated + recoverable."""

    NAME = "zz_owner_tmp"
    NAME2 = "zz_owner_tmp2"
    CODE = ('from typing import Any\n'
            'NAME = "zz_owner_tmp"\n'
            'DESCRIPTION = "temp owner test tool"\n'
            'SOURCE = "dtm_ai"\n'
            'CATEGORY = "read"\n'
            'RISK_LEVEL = "none"\n'
            'PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}\n'
            'def run(ctx, **_: Any):\n'
            '    return {"ok": True, "hello": "world"}\n')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "w.db"
        self.agent = build_agent(db_path=db)
        self.auth = AuthStore(db)
        self.auth.ensure_admin("secret")
        self.api = Api(self.agent, self.auth, SessionSigner(secret=b"0" * 32))
        # belt-and-braces cleanup of every artifact this test can create
        import sys as _sys
        skills = Path(__file__).resolve().parents[1] / "execution" / "skills"
        trash = Path(__file__).resolve().parents[1] / ".tmp" / "deleted_skills"
        def _scrub():
            for p in (skills / f"{self.NAME}.py", skills / f"{self.NAME}.py.bak",
                      skills / f"{self.NAME2}.py", skills / f"{self.NAME2}.py.bak",
                      trash / f"{self.NAME}.py", trash / f"{self.NAME2}.py"):
                try: p.unlink()
                except OSError: pass
            _sys.modules.pop(f"execution.skills.{self.NAME}", None)
            _sys.modules.pop(f"execution.skills.{self.NAME2}", None)
        self.addCleanup(_scrub)

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    def H(self, method, path, body=None, user="admin"):
        return self.api.handle(method, path, {}, body or {}, user)

    def test_full_lifecycle(self):
        # add
        r = self.H("POST", "/api/tools", {"name": self.NAME, "code": self.CODE})
        self.assertEqual(r.status, 200, r.payload)
        self.assertIsNotNone(self.agent.registry.get(self.NAME))
        # bad edit (syntax error) is rejected and the old code survives
        r = self.H("POST", f"/api/tools/{self.NAME}/code", {"code": "def broken(:"})
        self.assertEqual(r.status, 400)
        self.assertIn("world", self.H("GET", f"/api/tools/{self.NAME}/code").payload["code"])
        # good edit goes live (hot reload, no restart)
        r = self.H("POST", f"/api/tools/{self.NAME}/code", {"code": self.CODE.replace("world", "mars")})
        self.assertEqual(r.status, 200, r.payload)
        self.assertIn("mars", self.H("GET", f"/api/tools/{self.NAME}/code").payload["code"])
        # rename keeps the trust policy
        self.agent.audit.set_enabled(self.NAME, True)
        r = self.H("POST", f"/api/tools/{self.NAME}/rename", {"name": self.NAME2})
        self.assertEqual(r.status, 200, r.payload)
        self.assertIsNone(self.agent.registry.get(self.NAME))
        self.assertIsNotNone(self.agent.registry.get(self.NAME2))
        self.assertTrue(self.agent.audit.is_enabled(self.NAME2, False))
        # delete moves the file to the recoverable trash
        r = self.H("DELETE", f"/api/tools/{self.NAME2}")
        self.assertEqual(r.status, 200, r.payload)
        self.assertIsNone(self.agent.registry.get(self.NAME2))
        self.assertTrue((Path(__file__).resolve().parents[1] / ".tmp" / "deleted_skills" / f"{self.NAME2}.py").is_file())

    def test_move_and_group_rename(self):
        self.H("POST", "/api/tools", {"name": self.NAME, "code": self.CODE})
        # move the tool to a brand-new group — creating the group implicitly
        r = self.H("POST", f"/api/tools/{self.NAME}/source", {"source": "zz_custom"})
        self.assertEqual(r.status, 200, r.payload)
        self.assertEqual(self.agent.registry.get(self.NAME).source, "zz_custom")
        # rename that group — rewrites SOURCE on its (one) tool
        r = self.H("POST", "/api/tools/groups/rename", {"from": "zz_custom", "to": "zz_relabeled"})
        self.assertEqual(r.status, 200, r.payload)
        self.assertEqual(r.payload["moved"], [self.NAME])
        self.assertEqual(self.agent.registry.get(self.NAME).source, "zz_relabeled")
        # unknown group 404s
        self.assertEqual(self.H("POST", "/api/tools/groups/rename",
                                {"from": "ghost_grp", "to": "x_y"}).status, 404)

    def test_add_requires_valid_tool_shape(self):
        r = self.H("POST", "/api/tools", {"name": self.NAME, "code": "x = 1\n"})
        self.assertEqual(r.status, 400)
        self.assertIsNone(self.agent.registry.get(self.NAME))
        skills = Path(__file__).resolve().parents[1] / "execution" / "skills"
        self.assertFalse((skills / f"{self.NAME}.py").exists())   # failed add leaves nothing behind

    def test_non_admin_blocked(self):
        self.auth.create_user("tech", "pw12345678", role="user")
        r = self.api.handle("POST", "/api/tools", {}, {"name": self.NAME, "code": self.CODE}, "tech")
        self.assertEqual(r.status, 403)
