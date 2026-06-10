"""API router — pure, testable mapping of (method, path, body, user) -> Resp.

Thin layer over the same runtime the CLI uses. The HTTP server (server.py) handles
sockets/cookies and delegates here. Every /api route except login requires a valid
session (fail-closed). All mutations are audited via dispatch()/the stores.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from ..agent import Agent
from ..core import credentials
from ..core.config import get_config
from ..core.context import ToolContext
from ..core.dispatch import dispatch
from ..runtime import get_client_factory
from .auth import AuthStore, SessionSigner

SESSION_COOKIE = "dtm_session"


@dataclass
class Resp:
    status: int
    payload: Any
    set_cookie: Optional[str] = None
    clear_cookie: bool = False


class Api:
    def __init__(self, agent: Agent, auth: AuthStore, signer: SessionSigner,
                 *, session_ttl_min: int = 720) -> None:
        self.agent = agent
        self.auth = auth
        self.signer = signer
        self.ttl = session_ttl_min
        # fleet-count cache (stale-while-revalidate): serve instantly, refresh in the background only
        # when viewed and stale — so no constant polling of the vendor APIs.
        from ..core.config import get_config
        self._fleet_cache: dict[str, dict] = {}
        self._fleet_inflight: set[str] = set()
        self._fleet_lock = threading.Lock()
        self._fleet_ttl = get_config().int("DTM_FLEET_TTL_SEC", 300)
        from ..core.adminshell import AdminShell
        self.shell = AdminShell()                 # admin-only terminal (D-21); gated + audited below

    def handle(self, method: str, path: str, query: dict, body: dict,
               user: Optional[str]) -> Resp:
        # public
        if method == "POST" and path == "/api/login":
            return self._login(body)
        if method == "POST" and path == "/api/logout":
            return Resp(200, {"ok": True}, clear_cookie=True)
        if method == "GET" and path == "/api/branding":
            return Resp(200, self._branding())      # public: just the logo URL (login screen needs it)

        # everything else requires a session (fail-closed)
        if not user:
            return Resp(401, {"error": "authentication required"})
        role = self.auth.get_role(user)
        if role is None:
            return Resp(401, {"error": "user no longer exists"})  # deleted mid-session

        if method == "GET" and path == "/api/me":
            me = self.auth.get_user(user) or {"username": user, "role": role, "email": ""}
            me["pending_approvals"] = self.agent.approvals.count_pending()
            return Resp(200, me)
        if method == "POST" and path == "/api/me/password":
            return self._change_own_password(user, body)
        if method == "GET" and path == "/api/users":
            return self._require_admin(role) or Resp(200, {"users": self.auth.list_users()})
        if method == "POST" and path == "/api/users":
            return self._require_admin(role) or self._create_user(body, user)
        if method == "POST" and path.startswith("/api/users/"):
            return self._require_admin(role) or self._update_user(path.split("/")[3], body, user)
        if method == "DELETE" and path.startswith("/api/users/"):
            return self._require_admin(role) or self._delete_user(path.split("/")[3], user)
        if method == "GET" and path == "/api/memory":
            return self._memory(query.get("tenant") or "*")
        if method == "POST" and path == "/api/memory":
            return self._memory_save(body, user)
        if method == "GET" and path == "/api/kb":
            return self._kb_doc(query.get("doc") or "")
        if method == "POST" and path == "/api/kb":
            return self._require_admin(role) or self._kb_write(body, user)
        if method == "DELETE" and path == "/api/kb":
            return self._require_admin(role) or self._kb_delete(query.get("doc") or "", user)
        if method == "POST" and path == "/api/kb/rename":
            return self._require_admin(role) or self._kb_rename(body, user)
        if method == "GET" and path == "/api/terminal":
            return self._require_admin(role) or self._terminal_state(user)
        if method == "POST" and path == "/api/terminal":
            return self._require_admin(role) or self._terminal_run(body, user)
        if method == "GET" and path == "/api/clients":
            from ..core.memory import VaultStore
            return Resp(200, {"clients": VaultStore().list_clients()})
        if method == "POST" and path == "/api/clients":
            return self._require_admin(role) or self._client_add(body, user)
        if method == "DELETE" and path.startswith("/api/clients/") and len(path.split("/")) == 4:
            return self._require_admin(role) or self._client_remove(path.split("/")[3], user)
        if method == "GET" and path == "/api/build/candidates":
            from ..core import builder
            return self._require_admin(role) or Resp(200, {"candidates": builder.list_candidates()})
        if method == "POST" and path == "/api/build/draft":
            return self._require_admin(role) or self._build_draft(body, user)
        if method == "POST" and path.startswith("/api/build/") and path.endswith("/promote"):
            return self._require_admin(role) or self._build_promote(path.split("/")[3], user)
        if method == "POST" and path.startswith("/api/build/") and path.endswith("/reject"):
            return self._require_admin(role) or self._build_reject(path.split("/")[3], user)
        if method == "GET" and path == "/api/approvals":
            return Resp(200, {"approvals": self.agent.approvals.list(query.get("status") or None),
                              "pending": self.agent.approvals.count_pending()})
        if method == "POST" and path.startswith("/api/approvals/") and path.endswith("/approve"):
            return self._require_admin(role) or self._approve(int(path.split("/")[3]), user)
        if method == "POST" and path.startswith("/api/approvals/") and path.endswith("/reject"):
            return self._require_admin(role) or self._reject(int(path.split("/")[3]), user)
        if method == "POST" and path == "/api/branding/logo":
            return self._require_admin(role) or self._set_logo(body, user)
        if method == "DELETE" and path == "/api/branding/logo":
            return self._require_admin(role) or self._del_logo(user)
        if method == "GET" and path == "/api/fs/list":
            return self._require_admin(role) or self._fs_list(query.get("path") or "")
        if method == "GET" and path == "/api/fs/file":
            return self._require_admin(role) or self._fs_file(query.get("path") or "")
        if method == "POST" and path == "/api/fs/save":
            return self._require_admin(role) or self._fs_save(body, user)
        if method == "POST" and path == "/api/fs/upload":
            return self._require_admin(role) or self._fs_upload(body, user)
        if method == "POST" and path == "/api/fs/mkdir":
            return self._require_admin(role) or self._fs_mkdir(body, user)
        if method == "POST" and path == "/api/fs/chmod":
            return self._require_admin(role) or self._fs_chmod(body, user)
        if method == "POST" and path == "/api/fs/delete":
            return self._require_admin(role) or self._fs_delete(body, user)
        if method == "GET" and path == "/api/tools":
            return Resp(200, {"tools": self._tools()})
        if method == "GET" and path.startswith("/api/tools/") and path.endswith("/code"):
            return self._require_admin(role) or self._tool_code(path.split("/")[3], user)
        if method == "POST" and path == "/api/tools":
            return self._require_admin(role) or self._tool_add(body, user)
        if method == "POST" and path.startswith("/api/tools/") and path.endswith("/code"):
            return self._require_admin(role) or self._tool_edit(path.split("/")[3], body, user)
        if method == "POST" and path == "/api/tools/groups/rename":   # before the per-tool route
            return self._require_admin(role) or self._group_rename(body, user)
        if method == "POST" and path.startswith("/api/tools/") and path.endswith("/rename"):
            return self._require_admin(role) or self._tool_rename(path.split("/")[3], body, user)
        if method == "POST" and path.startswith("/api/tools/") and path.endswith("/source"):
            return self._require_admin(role) or self._tool_move(path.split("/")[3], body, user)
        if method == "DELETE" and path.startswith("/api/tools/") and len(path.split("/")) == 4:
            return self._require_admin(role) or self._tool_delete(path.split("/")[3], user)
        if method == "GET" and path == "/api/models":
            r = self.agent.router
            return Resp(200, {"models": r.available_models(),
                              "catalog": r.catalog_models(),
                              "context": {"history_chars": getattr(r, "history_chars", 16000),
                                          "history_msgs": getattr(r, "history_msgs", 30)}})
        if method == "GET" and path == "/api/integrations":
            return Resp(200, {"integrations": self._integrations()})
        if method == "GET" and path == "/api/integrations/probe":
            return Resp(200, {"probes": self._probe(query.get("integration"))})
        if method == "GET" and path.startswith("/api/integrations/") and path.endswith("/fields"):
            return self._integration_fields(path.split("/")[3])
        if method == "POST" and path.startswith("/api/integrations/") and path.endswith("/credentials"):
            return self._set_credentials(path.split("/")[3], body, user)
        if method == "GET" and path == "/api/capabilities":
            return Resp(200, {"capabilities": self._tools()})  # tools carry their policy
        if method == "GET" and path == "/api/skills":
            return Resp(200, self._skills())
        if method == "POST" and path == "/api/skills/learn":
            return self._require_admin(role) or self._learn_skill(body, user)
        if method == "DELETE" and path.startswith("/api/skills/") and len(path.split("/")) == 4:
            return self._require_admin(role) or self._delete_skill(path.split("/")[3], user)
        if method == "GET" and path == "/api/agents":
            from ..core.agents import list_agents
            return Resp(200, {"agents": list_agents()})
        if method == "POST" and path == "/api/agents":
            return self._require_admin(role) or self._create_agent(body, user)
        if method == "POST" and path == "/api/agents/roster/sync":
            return self._require_admin(role) or self._sync_roster(user)
        if method == "DELETE" and path.startswith("/api/agents/") and len(path.split("/")) == 4:
            return self._require_admin(role) or self._delete_agent(path.split("/")[3], user)
        if method == "GET" and path == "/api/agents/shared/ops":
            from ..core.agents import read_shared
            return Resp(200, {"text": read_shared()})
        if method == "POST" and path == "/api/agents/shared/ops":
            return self._require_admin(role) or self._set_shared_ops(body, user)
        if method == "GET" and path.startswith("/api/agents/") and path.endswith("/memory"):
            from ..core.agents import read_memory
            try:
                m = read_memory(path.split("/")[3])
            except ValueError:
                return Resp(400, {"error": "invalid agent name"})
            return Resp(200, m) if m else Resp(404, {"error": "unknown agent"})
        if method == "POST" and path.startswith("/api/agents/") and path.endswith("/memory"):
            return self._require_admin(role) or self._set_agent_memory(path.split("/")[3], body, user)
        if method == "POST" and path.startswith("/api/agents/") and path.endswith("/identity"):
            return self._require_admin(role) or self._set_agent_identity(path.split("/")[3], body, user)
        if method == "GET" and path.startswith("/api/agents/") and len(path.split("/")) == 4:
            from ..core.agents import get_agent
            try:
                a = get_agent(path.split("/")[3])
            except ValueError:
                return Resp(400, {"error": "invalid agent name"})
            return Resp(200, a) if a else Resp(404, {"error": "unknown agent"})
        if method == "POST" and path.startswith("/api/agents/") and path.endswith("/soul"):
            return self._require_admin(role) or self._set_agent_soul(path.split("/")[3], body, user)
        if method == "POST" and path.startswith("/api/agents/") and path.endswith("/brain"):
            return self._require_admin(role) or self._set_agent_brain(path.split("/")[3], body, user)
        if method == "GET" and path == "/api/kanban":
            return Resp(200, self.agent.tasks.board())
        if method == "GET" and path.startswith("/api/kanban/tasks/") and len(path.split("/")) == 5:
            t = self.agent.tasks.get(path.split("/")[4])
            return Resp(200, t) if t else Resp(404, {"error": "task not found"})
        if method == "POST" and path == "/api/kanban/tasks":
            return self._require_admin(role) or self._kanban_create(body, user)
        if method == "POST" and path.startswith("/api/kanban/tasks/") and path.endswith("/assign"):
            return self._require_admin(role) or self._kanban_assign(path.split("/")[4], body, user)
        if method == "POST" and path.startswith("/api/kanban/tasks/") and path.endswith("/archive"):
            return self._require_admin(role) or self._kanban_archive(path.split("/")[4], user)
        if method == "POST" and path.startswith("/api/kanban/tasks/") and path.endswith("/pause"):
            return self._require_admin(role) or self._kanban_pause(path.split("/")[4], body, user)
        if method == "POST" and path.startswith("/api/kanban/tasks/") and path.endswith("/run-now"):
            return self._require_admin(role) or self._kanban_run_now(path.split("/")[4], user)
        if method == "POST" and path == "/api/kanban/dispatch":
            return self._require_admin(role) or self._kanban_dispatch(user)
        if method == "POST" and path.startswith("/api/capabilities/"):
            return self._set_capability(path.rsplit("/", 1)[-1], body)
        if method == "GET" and path == "/api/audit":
            tenant = query.get("tenant") or None
            limit = int(query.get("limit") or 50)
            return Resp(200, {"audit": self.agent.audit.query(tenant_id=tenant, limit=limit)})
        if method == "POST" and path == "/api/chat":
            return self._chat(body, user)
        if method == "POST" and path == "/api/chat/compact":
            return Resp(200, {"summary": self.agent.summarize(body.get("history"),
                                                              model_id=body.get("model"))})
        # ── conversations (per-user persistent chat history) ──
        if method == "GET" and path == "/api/conversations":
            return Resp(200, {"conversations": self.agent.conversations.list(user)})
        if method == "POST" and path == "/api/conversations":
            return Resp(200, self.agent.conversations.create(
                user, tenant_id=body.get("tenant") or "*", title=(body.get("title") or "").strip()))
        if method == "POST" and path.startswith("/api/conversations/") and path.endswith("/rename"):
            ok = self.agent.conversations.rename(user, path.split("/")[3], body.get("title") or "")
            return Resp(200, {"ok": True}) if ok else Resp(404, {"error": "conversation not found"})
        if method == "POST" and path.startswith("/api/conversations/") and path.endswith("/compact"):
            return self._compact_conversation(path.split("/")[3], body, user)
        if method == "GET" and path.startswith("/api/conversations/"):
            conv = self.agent.conversations.get(user, path.split("/")[3])
            return Resp(200, conv) if conv else Resp(404, {"error": "conversation not found"})
        if method == "DELETE" and path.startswith("/api/conversations/"):
            ok = self.agent.conversations.delete(user, path.split("/")[3])
            return Resp(200, {"ok": True}) if ok else Resp(404, {"error": "conversation not found"})
        if method == "GET" and path == "/api/system/stats":
            from ..core import sysstats
            return Resp(200, sysstats.collect())
        if method == "GET" and path == "/api/fleet":
            return Resp(200, self._fleet(query.get("tenant") or "*", user,
                                         force=query.get("refresh") == "1"))

        return Resp(404, {"error": f"no route {method} {path}"})

    # ── handlers ──
    def _login(self, body: dict) -> Resp:
        role = self.auth.verify_login(body.get("username", ""), body.get("password", ""))
        if not role:
            return Resp(401, {"error": "invalid credentials"})
        token = self.signer.make(body["username"], self.ttl)
        return Resp(200, {"ok": True, "role": role}, set_cookie=token)

    def _tools(self) -> list[dict]:
        out = []
        for t in self.agent.registry.all():
            pol = self.agent.caps.get(t.name, default_enabled=t.enabled_by_default)
            out.append({
                "name": t.name, "description": t.description, "source": t.source,
                "category": t.category, "risk": t.risk_level,
                "enabled": self.agent.audit.is_enabled(t.name, t.enabled_by_default),
                "allow_write": pol.allow_write, "require_approval": pol.require_approval,
            })
        return out

    def _integrations(self) -> list[dict]:
        out = [{"integration": s.integration, "label": s.label,
                "kind": ("llm" if s.group == "llm" else "api"), "group": s.group,
                "configured": s.configured, "missing": s.missing, "fingerprints": s.fingerprints}
               for s in credentials.status()]
        # local (non-credential) integrations — Obsidian vault + learned skills
        from ..core.memory import VaultStore
        from ..core.playbooks import PlaybookStore
        v = VaultStore()
        kb, mems = v.list_kb(), v.list_client_memories()
        out.append({"integration": "obsidian", "label": "Obsidian Vault", "kind": "local",
                    "configured": v.root.exists(),
                    "detail": (f"{len(kb)} KB docs · {len(mems)} client notebooks" if v.root.exists()
                               else "vault not created yet"),
                    "path": str(v.root)})
        pb = PlaybookStore()
        skills = pb.list_skills()
        out.append({"integration": "skills", "label": "Learned Skills", "kind": "local",
                    "configured": True,
                    "detail": (f"{len(skills)} saved skill(s)" if skills else "no skills saved yet"),
                    "path": str(pb.root)})
        return out

    def _approve(self, approval_id: int, user: str) -> Resp:
        """Approve a pending action and EXECUTE it exactly as proposed (args-bound), once."""
        from ..core.gates import AlwaysApprove
        from ..runtime import get_client_factory
        row = self.agent.approvals.get(approval_id)
        if not row:
            return Resp(404, {"error": "approval not found"})
        if row["status"] != "pending":
            return Resp(409, {"error": f"already {row['status']}"})
        if not self.agent.approvals.claim_for_execution(approval_id, by=user):
            return Resp(409, {"error": "already decided"})
        ctx = ToolContext(tenant_id=row["tenant_id"], actor=f"{user} (approval#{approval_id})",
                          client_factory=get_client_factory())
        env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx,
                       name=row["tool"], args=row["args"], gate=AlwaysApprove())
        self.agent.approvals.mark_result(approval_id, bool(env["ok"]))
        self.agent.audit.record(actor=user, tenant_id=row["tenant_id"], action="approval_executed",
                                tool=row["tool"], category=row["category"], result_ok=bool(env["ok"]),
                                detail=f"approval#{approval_id}")
        return Resp(200, {"ok": True, "executed": env["ok"], "result": env})

    def _reject(self, approval_id: int, user: str) -> Resp:
        if not self.agent.approvals.reject(approval_id, by=user):
            return Resp(409, {"error": "not pending"})
        self.agent.audit.record(actor=user, tenant_id="*", action="approval_rejected",
                                detail=f"approval#{approval_id}")
        return Resp(200, {"ok": True})

    def _build_draft(self, body: dict, user: str) -> Resp:
        desc = (body.get("description") or "").strip()
        if not desc:
            return Resp(400, {"error": "describe the tool you want"})
        from ..core import builder
        r = builder.draft(desc, router=self.agent.router, model_id=body.get("model"))
        self.agent.audit.record(actor=user, tenant_id="*", action="build_draft",
                                tool=r.get("name"), detail=desc[:120])
        return Resp(200, r)

    def _build_promote(self, name: str, user: str) -> Resp:
        from ..core import builder
        r = builder.promote(name)
        if r.get("ok"):
            self.agent.registry.discover()   # make the new tool live (disabled by default)
            self.agent.audit.record(actor=user, tenant_id="*", action="build_promote", tool=name)
        return Resp(200 if r.get("ok") else 400, r)

    def _build_reject(self, name: str, user: str) -> Resp:
        from ..core import builder
        ok = builder.reject(name)
        self.agent.audit.record(actor=user, tenant_id="*", action="build_reject", tool=name)
        return Resp(200, {"ok": ok})

    def _memory(self, tenant: str) -> Resp:
        from ..core.memory import VaultStore
        v = VaultStore()
        text = "" if tenant in ("", "*") else v.read_memory(tenant)
        return Resp(200, {"tenant": tenant, "memory": text, "kb": v.list_kb(),
                          "clients": v.list_clients()})

    def _memory_save(self, body: dict, user: str) -> Resp:
        """Update a client's long-term memory (internal vault write; audited).
        `content` overwrites the whole memory (the living, editable record); `note` appends one fact."""
        from ..core.memory import VaultStore
        tenant = (body.get("tenant") or "").strip()
        v = VaultStore()
        if "content" in body:                              # full overwrite (edit / correct / prune)
            r = v.write_memory(tenant, body.get("content") or "", user)
            tool, detail = "memory_update", "memory_update (overwrite)"
        else:                                              # append a single new fact
            note = (body.get("note") or "").strip()
            if not note:
                return Resp(400, {"error": "note or content required"})
            r = v.append_memory(tenant, note, user)
            tool, detail = "memory_note", f"memory_add: {note[:80]}"
        if r.get("error"):
            return Resp(400, r)
        self.agent.audit.record(actor=user, tenant_id=tenant or "*", action="config_change",
                                tool=tool, detail=detail)
        return Resp(200, r)

    def _terminal_state(self, user: str) -> Resp:
        """Initial state for the admin Terminal tab: enabled flag, working dir, run-as user + host."""
        import getpass
        import socket
        from ..core.adminshell import terminal_enabled
        en = terminal_enabled()
        try:
            who, host = getpass.getuser(), socket.gethostname()
        except OSError:
            who, host = "dtm-ai", "server"
        return Resp(200, {"enabled": en, "cwd": self.shell.cwd(user) if en else None,
                          "user": who, "host": host})

    def _terminal_run(self, body: dict, user: str) -> Resp:
        """Run one shell command as the service user (admin-only, D-21). AUDITED BEFORE it runs, so
        even a command that kills the process leaves a record. Returns stdout/stderr/exit/cwd."""
        from ..core.adminshell import terminal_enabled
        if not terminal_enabled():
            return Resp(403, {"error": "admin terminal is disabled (DTM_ADMIN_TERMINAL=0)"})
        command = (body.get("command") or "").strip()
        if not command:
            return Resp(400, {"error": "command required"})
        self.agent.audit.record(actor=user, tenant_id="*", action="terminal",
                                detail=command[:500])
        return Resp(200, self.shell.run(user, command))

    def _kb_doc(self, doc: str) -> Resp:
        """Read one knowledge-base / reference doc by its listed path (no traversal)."""
        from ..core.memory import VaultStore
        if not doc:
            return Resp(400, {"error": "doc required"})
        content = VaultStore().read_kb_doc(doc)
        return (Resp(200, {"doc": doc, "content": content}) if content is not None
                else Resp(404, {"error": "doc not found"}))

    def _kb_write(self, body: dict, user: str) -> Resp:
        """Create/overwrite a KB doc under vault/kb/ (owner-gated; audited)."""
        from ..core.memory import VaultStore
        r = VaultStore().write_kb_doc(body.get("name") or "", body.get("content") or "")
        if r.get("error"):
            return Resp(400, r)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"kb_write={r['doc']}")
        return Resp(200, r)

    def _kb_delete(self, doc: str, user: str) -> Resp:
        """Delete an owner kb/ doc (reference/ docs are read-only). Owner-gated; audited."""
        from ..core.memory import VaultStore
        r = VaultStore().delete_kb_doc(doc)
        if r.get("error"):
            return Resp(400, r)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"kb_delete={doc}")
        return Resp(200, r)

    def _kb_rename(self, body: dict, user: str) -> Resp:
        """Rename/move an owner kb/ doc (reference/ docs are read-only). Owner-gated; audited."""
        from ..core.memory import VaultStore
        r = VaultStore().rename_kb_doc(body.get("from") or "", body.get("to") or "")
        if r.get("error"):
            return Resp(400, r)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"kb_rename={r['from']}->{r['to']}")
        return Resp(200, r)

    def _client_add(self, body: dict, user: str) -> Resp:
        """Register a client (tenant) so it can be selected. Owner-gated; audited."""
        from ..core.memory import VaultStore
        r = VaultStore().add_client(body.get("id") or "")
        if r.get("error"):
            return Resp(400, r)
        self.agent.audit.record(actor=user, tenant_id=r["id"], action="config_change",
                                detail=f"client_add={r['id']}")
        return Resp(200, r)

    def _client_remove(self, cid: str, user: str) -> Resp:
        """Remove a client + its saved memory (destructive). Owner-gated; audited."""
        from ..core.memory import VaultStore
        r = VaultStore().remove_client(cid)
        if r.get("error"):
            return Resp(404, r)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"client_remove={cid}")
        return Resp(200, r)

    # ── user accounts ───────────────────────────────────────────────────────
    def _require_admin(self, role: str) -> Optional[Resp]:
        return None if role == "admin" else Resp(403, {"error": "admin role required"})

    def _change_own_password(self, user: str, body: dict) -> Resp:
        if not self.auth.verify_login(user, body.get("current", "")):
            return Resp(400, {"error": "current password is incorrect"})
        newpw = body.get("new", "")
        if len(newpw) < 8:
            return Resp(400, {"error": "new password must be at least 8 characters"})
        self.auth.set_password(user, newpw)
        self.agent.audit.record(actor=user, tenant_id="*", action="password_change", tool=user)
        return Resp(200, {"ok": True})

    def _create_user(self, body: dict, actor: str) -> Resp:
        try:
            self.auth.create_user(body.get("username", ""), body.get("password", ""),
                                  body.get("role", "user"), body.get("email", ""))
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=actor, tenant_id="*", action="user_create",
                                tool=body.get("username", ""), detail=f"role={body.get('role','user')}")
        return Resp(200, {"ok": True, "users": self.auth.list_users()})

    def _update_user(self, name: str, body: dict, actor: str) -> Resp:
        try:
            self.auth.update_user(name, password=body.get("password") or None,
                                  role=body.get("role"), email=body.get("email"))
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=actor, tenant_id="*", action="user_update", tool=name)
        return Resp(200, {"ok": True, "users": self.auth.list_users()})

    def _delete_user(self, name: str, actor: str) -> Resp:
        if name == actor:
            return Resp(400, {"error": "you cannot delete your own account"})
        try:
            self.auth.delete_user(name)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=actor, tenant_id="*", action="user_delete", tool=name)
        return Resp(200, {"ok": True, "users": self.auth.list_users()})

    def _probe(self, integration: Optional[str]) -> dict:
        from ..clients import probe
        targets = [integration] if integration else ["kaseya", "cylance", "huntress"]
        return {t: probe(t) for t in targets}

    # live fleet counts (assets / agents / devices) for the dashboard — re-pulled on demand, so a
    # newly onboarded endpoint shows up the next time it's loaded/refreshed.
    _FLEET = [("kaseya_list_assets", "Kaseya assets", "monitor-dot"),
              ("huntress_list_agents", "Huntress agents", "radar"),
              ("cylance_list_devices", "Cylance devices", "shield")]

    def _fleet_compute(self, tenant: str, user: str) -> dict:
        ctx = ToolContext(tenant_id=tenant, actor=user, client_factory=get_client_factory())
        out = []
        for name, label, icon in self._FLEET:
            if self.agent.registry.get(name) is None or not self.agent.audit.is_enabled(name, True):
                continue
            env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx, name=name)
            data = env.get("data")
            count = len(data) if isinstance(data, list) else None
            out.append({"name": name, "label": label, "icon": icon, "ok": bool(env.get("ok")),
                        "count": count, "error": env.get("error")})
        return {"tenant": tenant, "fleet": out}

    def _fleet_refresh_async(self, tenant: str, user: str) -> None:
        with self._fleet_lock:
            if tenant in self._fleet_inflight:
                return                      # a refresh is already running for this tenant
            self._fleet_inflight.add(tenant)
        def _work():
            try:
                data = self._fleet_compute(tenant, user)
                with self._fleet_lock:
                    self._fleet_cache[tenant] = {"data": data, "ts": time.monotonic()}
            finally:
                with self._fleet_lock:
                    self._fleet_inflight.discard(tenant)
        threading.Thread(target=_work, daemon=True).start()

    def _fleet(self, tenant: str, user: str, force: bool = False) -> dict:
        now = time.monotonic()
        with self._fleet_lock:
            ent = self._fleet_cache.get(tenant)
        if force or not ent:                                # forced, or nothing cached yet → compute now
            data = self._fleet_compute(tenant, user)
            with self._fleet_lock:
                self._fleet_cache[tenant] = {"data": data, "ts": time.monotonic()}
            return {**data, "cached": False, "age_sec": 0, "ttl_sec": self._fleet_ttl}
        age = now - ent["ts"]
        if age >= self._fleet_ttl:                          # stale → serve stale now, refresh behind the scenes
            self._fleet_refresh_async(tenant, user)
        return {**ent["data"], "cached": True, "age_sec": int(age), "ttl_sec": self._fleet_ttl}

    def _skills(self) -> dict:
        """Saved learned-skill playbooks (native; replaces the Hermes skills reader)."""
        from ..core.playbooks import PlaybookStore
        s = PlaybookStore()
        return {"available": True, "dir": str(s.root), "skills": s.list_skills()}

    def _learn_skill(self, body: dict, user: str) -> Resp:
        """Save a multi-step turn as a reusable playbook (owner-confirmed; dedup'd; audited)."""
        from ..core.playbooks import PlaybookStore
        name = (body.get("name") or "").strip()
        if not name:
            return Resp(400, {"error": "skill name required"})
        try:
            r = PlaybookStore().save(
                name, description=body.get("description") or "",
                tools=body.get("tools") or [], when=body.get("when") or "",
                steps=body.get("steps") or "", tags=body.get("tags") or [],
                created_by=user, force=bool(body.get("force")))
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        except OSError as e:
            return Resp(500, {"error": f"cannot write skill: {e}"})
        if not r.get("ok"):                       # a near-duplicate exists — UI offers view/overwrite
            self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                    detail=f"skill_learn_dup={name}")
            return Resp(409, r)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"skill_learn={r.get('slug')}")
        return Resp(200, r)

    def _delete_skill(self, slug: str, user: str) -> Resp:
        """Delete a saved learned skill (owner-gated; audited)."""
        from ..core.playbooks import PlaybookStore
        try:
            r = PlaybookStore().delete(slug)
        except ValueError as e:
            return Resp(404, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"skill_delete={slug}")
        return Resp(200, r)

    @staticmethod
    def _suggest_skill(message: str, turn) -> Optional[dict]:
        """After a multi-step turn (>=2 distinct successful tools, excluding the skill lookup),
        suggest saving it as a reusable skill. The owner confirms/edits + saves via /api/skills/learn
        (which dedups). Returns None when there's nothing worth saving."""
        uniq = sorted({e["name"] for e in (getattr(turn, "tool_events", None) or [])
                       if e.get("ok") and e.get("name") and e["name"] != "skill_search"})
        if len(uniq) < 2:
            return None
        words = (message or "").strip().split()
        return {"proposed_name": " ".join(words[:6])[:60] or "New skill",
                "tools": uniq, "summary": (getattr(turn, "answer", "") or "")[:200]}

    def _create_agent(self, body: dict, user: str) -> Resp:
        """Add a new specialist agent = a fresh profile on disk (owner-gated; audited)."""
        from ..core.agents import create_agent
        name = (body.get("name") or "").strip().lower().replace(" ", "_")
        if not name:
            return Resp(400, {"error": "agent name required"})
        try:
            a = create_agent(name, soul=body.get("soul") or "",
                             description=body.get("description") or "", role=body.get("role") or "")
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        except FileExistsError as e:
            return Resp(409, {"error": str(e)})
        except OSError as e:
            return Resp(500, {"error": f"cannot create agent (config dir not writable?): {e}"})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"agent_create={name}")
        return Resp(200, a)

    def _sync_roster(self, user: str) -> Resp:
        """Rewrite AtlasOps' auto-maintained team roster from the live profiles (owner-gated; audited)."""
        from ..core.agents import sync_manager_roster
        try:
            r = sync_manager_roster()
        except OSError as e:
            return Resp(500, {"error": f"cannot write manager SOUL: {e}"})
        if r is None:
            return Resp(404, {"error": "no manager (default) SOUL found"})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"roster_sync ({r.get('count')} specialists)")
        return Resp(200, r)

    def _delete_agent(self, name: str, user: str) -> Resp:
        """Remove a specialist agent (the AtlasOps manager is protected; owner-gated; audited)."""
        from ..core.agents import delete_agent
        try:
            res = delete_agent(name)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        except FileNotFoundError as e:
            return Resp(404, {"error": str(e)})
        except OSError as e:
            return Resp(500, {"error": f"cannot delete agent: {e}"})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"agent_delete={name}")
        return Resp(200, res)

    def _kanban_create(self, body: dict, user: str) -> Resp:
        """Delegate: create a board task (optionally pre-assigned to a specialist). Owner-gated.
        An assigned task is dispatched immediately — a worker runs the agent loop AS that profile."""
        assignee = (body.get("assignee") or "").strip()
        tenant = (body.get("tenant") or "").strip()
        schedule = (body.get("schedule") or "").strip()
        next_run = None
        if schedule:                                   # optional recurrence (scheduled-delegation SOP)
            from ..core.scheduler import compute_next_run, valid_spec
            if not valid_spec(schedule):
                return Resp(400, {"error": f"unrecognised schedule '{schedule}' — try "
                                  "'every 30m', 'hourly', 'daily 07:00', or 'weekdays 09:30'"})
            if not assignee:
                return Resp(400, {"error": "a recurring task needs an assignee (specialist) to run it"})
            next_run = compute_next_run(schedule, int(time.time() * 1000))
        try:
            t = self.agent.tasks.create(
                body.get("title") or "", body=body.get("body") or "", assignee=assignee,
                created_by=f"dtm-ai:{user}", tenant=tenant,
                idempotency_key=(body.get("idempotency_key") or "").strip(),
                recurring=bool(schedule), schedule_spec=schedule, next_run_at=next_run)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        if assignee and not schedule:
            self.agent.dispatcher.dispatch()          # start it running now, not on the next poll
        self.agent.audit.record(actor=user, tenant_id=tenant or "*", action="config_change",
                                detail=f"delegate={assignee or 'unassigned'}:{t['title'][:60]}")
        return Resp(200, t)

    def _kanban_assign(self, task_id: str, body: dict, user: str) -> Resp:
        """Re/assign a task to a specialist profile ('none' to unassign). Owner-gated; audited."""
        profile = (body.get("profile") or "").strip()
        if not profile:
            return Resp(400, {"error": "profile required"})
        try:
            r = self.agent.tasks.assign(task_id, profile)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        if r.get("status") == "ready":                # newly dispatchable → kick a pass
            self.agent.dispatcher.dispatch()
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"delegate_assign={task_id}->{profile}")
        return Resp(200, r)

    def _kanban_archive(self, task_id: str, user: str) -> Resp:
        """Archive a finished task — clears it from the active board (owner-gated; audited)."""
        try:
            r = self.agent.tasks.archive(task_id)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"delegate_archive={task_id}")
        return Resp(200, r)

    def _kanban_pause(self, task_id: str, body: dict, user: str) -> Resp:
        """Pause/resume a recurring scheduled task — paused tasks never fire (owner-gated; audited)."""
        paused = bool(body.get("paused", True))
        try:
            r = self.agent.tasks.set_paused(task_id, paused)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"schedule_{'pause' if paused else 'resume'}={task_id}")
        return Resp(200, r)

    def _kanban_run_now(self, task_id: str, user: str) -> Resp:
        """Fire a scheduled task immediately (owner-gated; audited). Next dispatcher pass runs it."""
        try:
            r = self.agent.tasks.run_now(task_id)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.dispatcher.dispatch()
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"schedule_run_now={task_id}")
        return Resp(200, r)

    def _kanban_dispatch(self, user: str) -> Resp:
        """Force one dispatcher pass (owner-gated). Idempotent — only claims ready tasks."""
        r = self.agent.dispatcher.dispatch()
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"delegate_dispatch (spawned {r.get('spawned', 0)})")
        return Resp(200, r)

    def _set_agent_soul(self, name: str, body: dict, user: str) -> Resp:
        """Edit an agent's SOUL.md (owner-gated; audited). The agent loop loads it fresh next turn."""
        from ..core.agents import set_soul
        text = body.get("soul")
        if not isinstance(text, str) or not text.strip():
            return Resp(400, {"error": "soul text required"})
        try:
            a = set_soul(name, text)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        except FileNotFoundError as e:
            return Resp(404, {"error": str(e)})
        except OSError as e:
            return Resp(500, {"error": f"cannot write SOUL (config dir not writable?): {e}"})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"agent_soul={name}")
        return Resp(200, a)

    # ── branding — owner logo for the sidebar + login screen (admin-managed, publicly visible) ──
    _LOGO_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")
    _LOGO_MAX = 2 * 1024 * 1024

    def _branding_dir(self):
        from pathlib import Path
        d = get_config().get("DTM_BRANDING_DIR")    # test override; default = served /vendor dir
        return Path(d) if d else Path(__file__).resolve().parents[2] / "dashboard" / "vendor"

    def _logo_file(self):
        d = self._branding_dir()
        for ext in self._LOGO_EXTS:
            p = d / f"logo{ext}"
            if p.is_file():
                return p
        return None

    def _branding(self) -> dict:
        p = self._logo_file()
        return {"logo": f"/vendor/{p.name}?v={int(p.stat().st_mtime)}"} if p else {"logo": None}

    @staticmethod
    def _sniff_image(data: bytes):
        if data[:4] == b"\x89PNG":
            return ".png"
        if data[:2] == b"\xff\xd8":
            return ".jpg"
        if data[:4] == b"GIF8":
            return ".gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        head = data[:512].lstrip()
        if head.startswith(b"<") and b"<svg" in data[:2048].lower():
            return ".svg"
        return None

    def _set_logo(self, body: dict, user: str) -> Resp:
        import base64
        try:
            data = base64.b64decode(body.get("content_b64") or "", validate=True)
        except Exception:
            return Resp(400, {"error": "content_b64 is not valid base64"})
        if not data:
            return Resp(400, {"error": "empty file"})
        if len(data) > self._LOGO_MAX:
            return Resp(400, {"error": "logo too large (2 MB max)"})
        ext = self._sniff_image(data)
        if ext is None:
            return Resp(400, {"error": "not a recognised image (png / jpg / gif / webp / svg)"})
        d = self._branding_dir()
        d.mkdir(parents=True, exist_ok=True)
        for old in self._LOGO_EXTS:                  # one logo at a time
            try:
                (d / f"logo{old}").unlink()
            except OSError:
                pass
        (d / f"logo{ext}").write_bytes(data)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"branding_logo set ({len(data)} bytes, {ext})")
        return Resp(200, {"ok": True, **self._branding()})

    def _del_logo(self, user: str) -> Resp:
        removed = False
        d = self._branding_dir()
        for ext in self._LOGO_EXTS:
            try:
                (d / f"logo{ext}").unlink()
                removed = True
            except OSError:
                pass
        if removed:
            self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                    detail="branding_logo removed")
        return Resp(200, {"ok": True, "logo": None})

    # ── files manager — admin-only, runs as the service user, mutations audited (SOP: admin-terminal) ──
    _FS_ROOTS = ("/opt/dtm-ai", "/home", "/srv", "/etc", "/var/log", "/")
    _FS_PREVIEW_MAX = 256 * 1024
    _FS_UPLOAD_MAX = 25 * 1024 * 1024

    @staticmethod
    def _fs_norm(p: str):
        import os
        from pathlib import Path
        return Path(os.path.realpath(p or "/"))

    def _fs_list(self, pathq: str) -> Resp:
        import stat as _stat
        p = self._fs_norm(pathq or "/opt/dtm-ai")
        if not p.is_dir():
            return Resp(404, {"error": f"not a directory: {p}"})
        entries = []
        try:
            for c in p.iterdir():
                try:
                    st = c.lstat()
                    entries.append({
                        "name": c.name, "path": str(c), "dir": c.is_dir(),
                        "size": st.st_size, "mode": _stat.filemode(st.st_mode),
                        "octal": oct(st.st_mode & 0o7777)[2:], "mtime": int(st.st_mtime),
                        "hidden": c.name.startswith("."), "link": c.is_symlink(),
                    })
                except OSError:
                    continue
        except PermissionError as e:
            return Resp(403, {"error": str(e)})
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return Resp(200, {"path": str(p), "parent": (str(p.parent) if str(p) != "/" else None),
                          "roots": list(self._FS_ROOTS), "entries": entries})

    def _fs_file(self, pathq: str) -> Resp:
        p = self._fs_norm(pathq)
        if not p.is_file():
            return Resp(404, {"error": f"not a file: {p}"})
        try:
            size = p.stat().st_size
            raw = p.open("rb").read(self._FS_PREVIEW_MAX + 1)
        except OSError as e:
            return Resp(403, {"error": str(e)})
        if b"\x00" in raw[:8000]:
            return Resp(200, {"ok": False, "path": str(p), "size": size, "binary": True,
                              "error": "binary file — download it instead"})
        truncated = len(raw) > self._FS_PREVIEW_MAX
        return Resp(200, {"ok": True, "path": str(p), "size": size, "truncated": truncated,
                          "content": raw[:self._FS_PREVIEW_MAX].decode("utf-8", "replace")})

    def fs_download(self, pathq: str, user: Optional[str]):
        """Raw download — returns Resp on error, else (filename, bytes). Streamed by server.py."""
        if not user:
            return Resp(401, {"error": "authentication required"})
        if self.auth.get_role(user) != "admin":
            return Resp(403, {"error": "admin only"})
        p = self._fs_norm(pathq)
        if not p.is_file():
            return Resp(404, {"error": f"not a file: {p}"})
        try:
            data = p.read_bytes()
        except OSError as e:
            return Resp(403, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="file_download", detail=str(p))
        return (p.name, data)

    def _fs_save(self, body: dict, user: str) -> Resp:
        p = self._fs_norm(body.get("path") or "")
        content = body.get("content")
        if not isinstance(content, str):
            return Resp(400, {"error": "content required"})
        if p.is_dir():
            return Resp(400, {"error": "that is a directory"})
        try:
            p.write_text(content, encoding="utf-8")
        except OSError as e:
            return Resp(403, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="file_write", detail=str(p))
        return Resp(200, {"ok": True, "path": str(p), "size": p.stat().st_size})

    def _fs_upload(self, body: dict, user: str) -> Resp:
        import base64
        d = self._fs_norm(body.get("dir") or "")
        name = (body.get("name") or "").strip()
        if not d.is_dir():
            return Resp(404, {"error": f"not a directory: {d}"})
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            return Resp(400, {"error": "invalid file name"})
        try:
            data = base64.b64decode(body.get("content_b64") or "", validate=True)
        except Exception:
            return Resp(400, {"error": "content_b64 is not valid base64"})
        if len(data) > self._FS_UPLOAD_MAX:
            return Resp(400, {"error": "file too large (25 MB max)"})
        target = d / name
        try:
            target.write_bytes(data)
        except OSError as e:
            return Resp(403, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="file_upload",
                                detail=f"{target} ({len(data)} bytes)")
        return Resp(200, {"ok": True, "path": str(target), "size": len(data)})

    def _fs_mkdir(self, body: dict, user: str) -> Resp:
        d = self._fs_norm(body.get("dir") or "")
        name = (body.get("name") or "").strip()
        if not d.is_dir():
            return Resp(404, {"error": f"not a directory: {d}"})
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            return Resp(400, {"error": "invalid folder name"})
        try:
            (d / name).mkdir(exist_ok=False)
        except OSError as e:
            return Resp(403, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="file_mkdir", detail=str(d / name))
        return Resp(200, {"ok": True, "path": str(d / name)})

    def _fs_chmod(self, body: dict, user: str) -> Resp:
        import re as _re
        p = self._fs_norm(body.get("path") or "")
        mode = (body.get("mode") or "").strip()
        if not p.exists():
            return Resp(404, {"error": f"no such path: {p}"})
        if not _re.fullmatch(r"[0-7]{3,4}", mode):
            return Resp(400, {"error": "mode must be octal like 644 or 0755"})
        try:
            p.chmod(int(mode, 8))
        except OSError as e:
            return Resp(403, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="file_chmod", detail=f"{p} -> {mode}")
        return Resp(200, {"ok": True, "path": str(p), "mode": mode})

    def _fs_delete(self, body: dict, user: str) -> Resp:
        import shutil
        p = self._fs_norm(body.get("path") or "")
        if str(p) in ("/", "/opt/dtm-ai"):
            return Resp(400, {"error": "refusing to delete that path"})
        if not p.exists() and not p.is_symlink():
            return Resp(404, {"error": f"no such path: {p}"})
        try:
            if p.is_dir() and not p.is_symlink():
                if not body.get("recursive"):
                    return Resp(400, {"error": "directory — set recursive=true to delete it and its contents"})
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError as e:
            return Resp(403, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="file_delete", detail=str(p))
        return Resp(200, {"ok": True, "deleted": str(p)})

    def _tool_code(self, name: str, user: str) -> Resp:
        """READ-ONLY source view of a live skill (admin; audited). Editing stays out of the
        dashboard on purpose: live tool code is git-tracked and changes only via the Build
        sandbox + human merge (Invariants I-5 / I-6)."""
        import importlib
        import inspect
        info = self.agent.registry.get(name)
        if info is None:
            return Resp(404, {"error": f"unknown tool '{name}'"})
        try:
            mod = importlib.import_module(info.module)
            src = inspect.getsource(mod)
            path_ = inspect.getsourcefile(mod) or info.module
        except (OSError, TypeError) as e:
            return Resp(500, {"error": f"cannot read source: {e}"})
        self.agent.audit.record(actor=user, tenant_id="*", action="code_view", tool=name)
        return Resp(200, {"name": name, "module": info.module, "path": str(path_), "code": src})

    # ── owner direct tool authoring (D-23) — admin-only, audited, validated, hot-reloaded ────────
    _TOOL_NAME_RE = __import__("re").compile(r"^[a-z][a-z0-9_]*$")

    def _skills_dir(self):
        import execution.skills as pkg
        from pathlib import Path
        return Path(pkg.__path__[0])

    def _reload_skill(self, module_name: str) -> Optional[str]:
        """Import/reload a skill module + re-discover the registry. Error string or None."""
        import importlib
        import sys
        try:
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
            else:
                importlib.import_module(module_name)
            self.agent.registry.discover()
        except Exception as e:                       # surfaced verbatim to the admin
            return f"{type(e).__name__}: {e}"
        return None

    def _tool_edit(self, name: str, body: dict, user: str) -> Resp:
        """Overwrite a live skill's source (D-23). Validated before it sticks: syntax, import,
        and the module must still register a tool named `name` (renames go through /rename).
        Any failure restores the previous file byte-for-byte."""
        import ast as _ast
        import sys
        code = body.get("code")
        if not isinstance(code, str) or not code.strip():
            return Resp(400, {"error": "code required"})
        info = self.agent.registry.get(name)
        if info is None:
            return Resp(404, {"error": f"unknown tool '{name}'"})
        try:
            _ast.parse(code)
        except SyntaxError as e:
            return Resp(400, {"error": f"syntax error: {e}"})
        path = self._skills_dir() / (info.module.rsplit(".", 1)[-1] + ".py")
        prev = path.read_text(encoding="utf-8")
        path.with_suffix(".py.bak").write_text(prev, encoding="utf-8")
        path.write_text(code, encoding="utf-8")
        err = self._reload_skill(info.module)
        if err is None and self.agent.registry.get(name) is None:
            err = (f"edited code no longer defines a valid tool named '{name}' "
                   "(NAME/DESCRIPTION/PARAMETERS/run required; to rename, use Rename)")
        if err:
            path.write_text(prev, encoding="utf-8")
            self._reload_skill(info.module)          # restore the old version in memory too
            return Resp(400, {"error": err})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"tool_edit={name}")
        return Resp(200, {"ok": True, "name": name})

    def _tool_add(self, body: dict, user: str) -> Resp:
        """Create a new live skill from owner-pasted code (D-23). NAME must equal the file name."""
        import ast as _ast
        import sys
        name = (body.get("name") or "").strip().lower()
        code = body.get("code")
        if not self._TOOL_NAME_RE.match(name):
            return Resp(400, {"error": "name must be snake_case (lowercase letters, digits, _)"})
        if not isinstance(code, str) or not code.strip():
            return Resp(400, {"error": "code required"})
        path = self._skills_dir() / f"{name}.py"
        if path.exists() or self.agent.registry.get(name) is not None:
            return Resp(409, {"error": f"tool '{name}' already exists"})
        try:
            _ast.parse(code)
        except SyntaxError as e:
            return Resp(400, {"error": f"syntax error: {e}"})
        path.write_text(code, encoding="utf-8")
        module = f"execution.skills.{name}"
        err = self._reload_skill(module)
        if err is None and self.agent.registry.get(name) is None:
            err = (f"module imports but does not register a tool named '{name}' — NAME must equal "
                   "the file name and NAME/DESCRIPTION/PARAMETERS/run must all be defined")
        if err:
            try:
                path.unlink()
            except OSError:
                pass
            sys.modules.pop(module, None)
            self.agent.registry.discover()
            return Resp(400, {"error": err})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"tool_add={name}")
        return Resp(200, {"ok": True, "name": name})

    def _tool_rename(self, name: str, body: dict, user: str) -> Resp:
        """Rename a live skill (D-23): rewrites the NAME line + the file, migrates its
        enabled/allow_write/require_approval policy so trust settings survive the rename."""
        import re as _re
        import sys
        new = (body.get("name") or "").strip().lower()
        if not self._TOOL_NAME_RE.match(new):
            return Resp(400, {"error": "new name must be snake_case (lowercase letters, digits, _)"})
        info = self.agent.registry.get(name)
        if info is None:
            return Resp(404, {"error": f"unknown tool '{name}'"})
        if new == name:
            return Resp(400, {"error": "that is already the tool's name"})
        new_path = self._skills_dir() / f"{new}.py"
        if new_path.exists() or self.agent.registry.get(new) is not None:
            return Resp(409, {"error": f"tool '{new}' already exists"})
        old_path = self._skills_dir() / (info.module.rsplit(".", 1)[-1] + ".py")
        src = old_path.read_text(encoding="utf-8")
        src2, n = _re.subn(r"(?m)^(NAME\s*=\s*)(['\"]).*?\2", rf"\g<1>\g<2>{new}\g<2>", src, count=1)
        if n != 1:
            return Resp(400, {"error": "couldn't find a simple NAME = \"…\" line to rewrite — edit the code instead"})
        # capture the current policy BEFORE the registry forgets the old name
        was_enabled = self.agent.audit.is_enabled(name, info.enabled_by_default)
        pol = self.agent.caps.get(name, default_enabled=info.enabled_by_default)
        new_path.write_text(src2, encoding="utf-8")
        old_path.unlink()
        sys.modules.pop(info.module, None)
        err = self._reload_skill(f"execution.skills.{new}")
        if err is None and self.agent.registry.get(new) is None:
            err = "renamed module no longer registers a valid tool"
        if err:
            old_path.write_text(src, encoding="utf-8")   # roll back
            try:
                new_path.unlink()
            except OSError:
                pass
            sys.modules.pop(f"execution.skills.{new}", None)
            self._reload_skill(info.module)
            return Resp(400, {"error": err})
        self.agent.audit.set_enabled(new, was_enabled)   # trust settings follow the tool
        self.agent.caps.set(new, allow_write=pol.allow_write, require_approval=pol.require_approval)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"tool_rename={name}->{new}")
        return Resp(200, {"ok": True, "name": new, "was": name})

    def _rewrite_source(self, info, new_source: str) -> Optional[str]:
        """Rewrite (or insert, anchored after NAME) the SOURCE line in a skill file, then reload.
        Restores the previous file on any failure. Returns an error string or None."""
        import re as _re
        path = self._skills_dir() / (info.module.rsplit(".", 1)[-1] + ".py")
        src = path.read_text(encoding="utf-8")
        s2, n = _re.subn(r"(?m)^(SOURCE\s*=\s*)(['\"]).*?\2",
                         rf"\g<1>\g<2>{new_source}\g<2>", src, count=1)
        if n == 0:   # module relied on the NAME-prefix default — make SOURCE explicit
            s2, n = _re.subn(r"(?m)^(NAME\s*=\s*['\"].*?['\"].*)$",
                             rf'\g<1>\nSOURCE = "{new_source}"', src, count=1)
            if n == 0:
                return "couldn't find a NAME line to anchor a SOURCE assignment — edit the code instead"
        path.write_text(s2, encoding="utf-8")
        err = self._reload_skill(info.module)
        if err is None:
            t = self.agent.registry.get(info.name)
            if t is None or t.source != new_source:
                err = "the SOURCE edit didn't take — edit the code by hand instead"
        if err:
            path.write_text(src, encoding="utf-8")
            self._reload_skill(info.module)
            return err
        return None

    def _tool_move(self, name: str, body: dict, user: str) -> Resp:
        """Move ONE tool to another group (D-23): the Capabilities groups ARE the tools' SOURCE
        labels, so this rewrites the SOURCE line. A new group name simply creates that group."""
        new = (body.get("source") or "").strip().lower()
        if not self._TOOL_NAME_RE.match(new):
            return Resp(400, {"error": "group must be snake_case (lowercase letters, digits, _)"})
        info = self.agent.registry.get(name)
        if info is None:
            return Resp(404, {"error": f"unknown tool '{name}'"})
        if info.source == new:
            return Resp(400, {"error": f"'{name}' is already in group '{new}'"})
        err = self._rewrite_source(info, new)
        if err:
            return Resp(400, {"error": err})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"tool_move={name}:{info.source}->{new}")
        return Resp(200, {"ok": True, "name": name, "source": new})

    def _group_rename(self, body: dict, user: str) -> Resp:
        """Rename a whole group (D-23): rewrites SOURCE for every tool currently in it."""
        frm = (body.get("from") or "").strip().lower()
        to = (body.get("to") or "").strip().lower()
        if not self._TOOL_NAME_RE.match(to):
            return Resp(400, {"error": "new group must be snake_case (lowercase letters, digits, _)"})
        if frm == to:
            return Resp(400, {"error": "that is already the group's name"})
        tools = [t for t in self.agent.registry.all() if t.source == frm]
        if not tools:
            return Resp(404, {"error": f"no tools in group '{frm}'"})
        moved = []
        for t in tools:
            err = self._rewrite_source(t, to)
            if err:   # stop at the first failure; already-moved tools stay moved (each was atomic)
                return Resp(500, {"error": f"failed at '{t.name}': {err}", "moved": moved})
            moved.append(t.name)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"group_rename={frm}->{to} ({len(moved)} tools)")
        return Resp(200, {"ok": True, "from": frm, "to": to, "moved": moved})

    def _tool_delete(self, name: str, user: str) -> Resp:
        """Delete a live skill (D-23): the file moves to .tmp/deleted_skills/ (recoverable), the
        module unloads, the registry re-discovers."""
        import shutil
        import sys
        from pathlib import Path
        info = self.agent.registry.get(name)
        if info is None:
            return Resp(404, {"error": f"unknown tool '{name}'"})
        path = self._skills_dir() / (info.module.rsplit(".", 1)[-1] + ".py")
        trash = Path(self._skills_dir()).parents[1] / ".tmp" / "deleted_skills"
        trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(trash / f"{name}.py"))
        sys.modules.pop(info.module, None)
        self.agent.registry.discover()
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"tool_delete={name}")
        return Resp(200, {"ok": True, "deleted": name, "recoverable_at": str(trash / f"{name}.py")})

    def _set_agent_memory(self, name: str, body: dict, user: str) -> Resp:
        """Owner-edit an agent's MEMORY.md / USER.md (admin; audited). Omitted field = untouched."""
        from ..core.agents import set_memory
        memory = body.get("memory") if isinstance(body.get("memory"), str) else None
        user_md = body.get("user") if isinstance(body.get("user"), str) else None
        if memory is None and user_md is None:
            return Resp(400, {"error": "memory and/or user text required"})
        try:
            m = set_memory(name, memory=memory, user=user_md)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        except FileNotFoundError as e:
            return Resp(404, {"error": str(e)})
        except OSError as e:
            return Resp(500, {"error": f"cannot write memory: {e}"})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"agent_memory={name}")
        return Resp(200, m)

    def _set_agent_identity(self, name: str, body: dict, user: str) -> Resp:
        """Owner-edit an agent's identity — name/role (SOUL) + emoji/accent/blurb (profile.yaml)."""
        from ..core.agents import set_identity
        fields = {k: body.get(k) for k in ("name", "role", "emoji", "accent", "description")
                  if isinstance(body.get(k), str)}
        if not fields:
            return Resp(400, {"error": "nothing to update"})
        try:
            a = set_identity(name, display_name=fields.get("name"), role=fields.get("role"),
                             emoji=fields.get("emoji"), accent=fields.get("accent"),
                             description=fields.get("description"))
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        except FileNotFoundError as e:
            return Resp(404, {"error": str(e)})
        except OSError as e:
            return Resp(500, {"error": f"cannot write identity: {e}"})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"agent_identity={name}")
        return Resp(200, a)

    def _set_shared_ops(self, body: dict, user: str) -> Resp:
        """Owner-edit the shared operating block appended to every agent's prompt (admin; audited)."""
        from ..core.agents import write_shared
        text = body.get("text")
        if not isinstance(text, str):
            return Resp(400, {"error": "text required"})
        try:
            r = write_shared(text)
        except OSError as e:
            return Resp(500, {"error": f"cannot write SHARED.md: {e}"})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail="agents_shared_ops")
        return Resp(200, r)

    def _set_agent_brain(self, name: str, body: dict, user: str) -> Resp:
        """Pin (or clear) an agent's brain — the model it runs on (owner-gated; audited). Validated
        against the FULL catalog, so a Claude brain can be set before the API key exists; it goes
        live once the key is added. '' / 'default' clears the pin (back to the run default)."""
        from ..core.agents import set_brain
        model = (body.get("model") or "").strip()
        if model and model not in ("default",) and not self.agent.router.is_catalog_model(model):
            return Resp(400, {"error": f"unknown model '{model}'"})
        try:
            a = set_brain(name, model)
        except FileNotFoundError as e:
            return Resp(404, {"error": str(e)})
        except (ValueError, OSError) as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"agent_brain={name}:{model or 'default'}")
        return Resp(200, a)

    def _integration_fields(self, name: str) -> Resp:
        """The credential fields for an integration (which are set, fingerprints) — never raw."""
        spec = credentials.SPECS.get(name)
        if spec is None:
            return Resp(404, {"error": f"unknown integration '{name}'"})
        import os
        from ..core.config import fingerprint, get_config
        cfg = get_config()
        # A non-empty value in the process env (e.g. systemd EnvironmentFile / .env) OUTRANKS the
        # SecretStore, so editing the key in the UI has no effect until that env value is removed.
        # Flag it so the dashboard can warn instead of silently ignoring the edit.
        fields = [{"key": k, "required": k in spec.required,
                   "set": cfg.present(k), "fingerprint": fingerprint(cfg.get(k)) if cfg.present(k) else None,
                   "shadowed": bool(os.environ.get(k))}
                  for k in (*spec.required, *spec.optional)]
        return Resp(200, {"integration": name, "label": spec.display, "fields": fields})

    def _set_credentials(self, name: str, body: dict, user: str) -> Resp:
        """Securely store credentials entered in the UI. Values never echoed back."""
        if not isinstance(body, dict) or not body:
            return Resp(400, {"error": "no credential values provided"})
        try:
            new_status = credentials.set_integration(name, {k: str(v) for k, v in body.items()})
        except credentials.MissingCredential as e:
            return Resp(400, {"error": str(e)})
        # take effect immediately + audit (keys only, never values)
        try:
            from ..runtime import get_client_factory
            get_client_factory().invalidate(name)
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id="*", action="credential_set",
                                tool=name, detail=f"keys: {','.join(sorted(body.keys()))}")
        return Resp(200, {"integration": name, "configured": new_status.configured,
                          "fingerprints": new_status.fingerprints, "missing": new_status.missing})

    def _set_capability(self, name: str, body: dict) -> Resp:
        if self.agent.registry.get(name) is None:
            return Resp(404, {"error": f"unknown tool '{name}'"})
        if "enabled" in body:
            self.agent.audit.set_enabled(name, bool(body["enabled"]))
        kw = {k: bool(body[k]) for k in ("allow_write", "require_approval") if k in body}
        pol = self.agent.caps.set(name, **kw) if kw else \
            self.agent.caps.get(name, default_enabled=True)
        return Resp(200, {"name": name,
                          "enabled": self.agent.audit.is_enabled(name, True),
                          "allow_write": pol.allow_write,
                          "require_approval": pol.require_approval})

    def _chat(self, body: dict, user: str) -> Resp:
        message = (body.get("message") or "").strip()
        if not message:
            return Resp(400, {"error": "message is required"})
        convs = self.agent.conversations
        model_id = body.get("model")
        # Resolve the conversation: reuse the caller's (if they own it) or open a fresh one.
        conv_id = body.get("conversation_id")
        if conv_id and convs.owns(user, conv_id):
            tenant = convs.tenant_of(user, conv_id) or "*"   # the conversation owns its tenant
        else:
            tenant = body.get("tenant") or "*"
            conv_id = convs.create(user, tenant_id=tenant)["id"]
        ctx = ToolContext(tenant_id=tenant, actor=user,
                          allow_cloud=bool(model_id and not model_id.startswith("ollama:")),
                          client_factory=get_client_factory(),
                          _meta={"tasks": self.agent.tasks})   # lets schedule_task reach the board
        # Server-side history is authoritative (the browser no longer holds the transcript).
        history = convs.history(user, conv_id)
        convs.add_message(user, conv_id, "user", message)
        turn = self.agent.chat(ctx, message, model_id=model_id, history=history,
                               profile=body.get("agent") or body.get("profile"))
        convs.add_message(user, conv_id, "assistant", turn.answer, meta={
            "tools": turn.tool_events, "citations": turn.citations,
            "label": f"{turn.provider}/{turn.model} · {turn.rounds} round(s)"})
        title = next((c["title"] for c in convs.list(user) if c["id"] == conv_id), "")
        return Resp(200, {
            "answer": turn.answer, "citations": turn.citations,
            "tool_events": turn.tool_events, "provider": turn.provider,
            "model": turn.model, "rounds": turn.rounds, "tenant": tenant,
            "conversation_id": conv_id, "title": title,
            "suggest_skill": self._suggest_skill(message, turn),
        })

    def stream_chat(self, body: dict, user: str) -> Iterator[dict]:
        """SSE event generator for streaming chat. Yields dicts (the server frames them as
        `data: {json}`). Mirrors _chat's persistence + tenant binding. The agent's push-callback
        is bridged to this pull-generator via a queue + worker thread so events flow in real time.
        Event types: start · tool_call · tool_result · delta · answer · error."""
        message = (body.get("message") or "").strip()
        if not message:
            yield {"type": "error", "error": "message is required"}
            return
        convs = self.agent.conversations
        model_id = body.get("model")
        conv_id = body.get("conversation_id")
        if conv_id and convs.owns(user, conv_id):
            tenant = convs.tenant_of(user, conv_id) or "*"
        else:
            tenant = body.get("tenant") or "*"
            conv_id = convs.create(user, tenant_id=tenant)["id"]
        prior = convs.history(user, conv_id)
        convs.add_message(user, conv_id, "user", message)
        yield {"type": "start", "conversation_id": conv_id, "tenant": tenant}

        q: "queue.Queue" = queue.Queue()
        DONE = object()
        result: dict = {}

        def run():
            try:
                ctx = ToolContext(
                    tenant_id=tenant, actor=user,
                    allow_cloud=bool(model_id and not model_id.startswith("ollama:")),
                    client_factory=get_client_factory(),
                    _meta={"tasks": self.agent.tasks})   # lets schedule_task reach the board
                result["turn"] = self.agent.chat_stream(
                    ctx, message, lambda e: q.put(e), model_id=model_id, history=prior,
                    profile=body.get("agent") or body.get("profile"))
            except Exception as e:                       # contained; surfaced as an SSE error frame
                result["error"] = str(e)
            finally:
                q.put(DONE)

        threading.Thread(target=run, daemon=True).start()
        while True:
            ev = q.get()
            if ev is DONE:
                break
            yield ev

        if "error" in result:
            yield {"type": "error", "error": result["error"]}
            return
        turn = result["turn"]
        convs.add_message(user, conv_id, "assistant", turn.answer, meta={
            "tools": turn.tool_events, "citations": turn.citations,
            "label": f"{turn.provider}/{turn.model} · {turn.rounds} round(s)"})
        title = next((c["title"] for c in convs.list(user) if c["id"] == conv_id), "")
        yield {"type": "answer", "answer": turn.answer, "citations": turn.citations,
               "tool_events": turn.tool_events, "provider": turn.provider, "model": turn.model,
               "rounds": turn.rounds, "tenant": tenant, "conversation_id": conv_id, "title": title,
               "suggest_skill": self._suggest_skill(message, turn)}

    def _compact_conversation(self, conv_id: str, body: dict, user: str) -> Resp:
        convs = self.agent.conversations
        conv = convs.get(user, conv_id)
        if not conv:
            return Resp(404, {"error": "conversation not found"})
        msgs = conv.get("messages") or []
        if len(msgs) < 4:
            return Resp(400, {"error": "not enough conversation to compact yet"})
        keep = 2
        older = [{"role": m["role"], "content": m["content"]} for m in msgs[:-keep] if m["content"]]
        summary = self.agent.summarize(older, model_id=body.get("model"))
        convs.compact(user, conv_id, summary, keep=keep)
        return Resp(200, convs.get(user, conv_id))
