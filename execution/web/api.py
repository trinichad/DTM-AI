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
        if method == "GET" and path == "/api/tools":
            return Resp(200, {"tools": self._tools()})
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
        if method == "GET" and path.startswith("/api/agents/") and path.endswith("/memory"):
            from ..core.agents import read_memory
            try:
                m = read_memory(path.split("/")[3])
            except ValueError:
                return Resp(400, {"error": "invalid agent name"})
            return Resp(200, m) if m else Resp(404, {"error": "unknown agent"})
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
        try:
            t = self.agent.tasks.create(
                body.get("title") or "", body=body.get("body") or "", assignee=assignee,
                created_by=f"dtm-ai:{user}", tenant=tenant,
                idempotency_key=(body.get("idempotency_key") or "").strip())
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        if assignee:
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
                          client_factory=get_client_factory())
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
                    client_factory=get_client_factory())
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
