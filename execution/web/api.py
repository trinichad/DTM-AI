"""API router — pure, testable mapping of (method, path, body, user) -> Resp.

Thin layer over the same runtime the CLI uses. The HTTP server (server.py) handles
sockets/cookies and delegates here. Every /api route except login requires a valid
session (fail-closed). All mutations are audited via dispatch()/the stores.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..agent import Agent
from ..core import credentials
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
        if method == "GET" and path == "/api/tools":
            return Resp(200, {"tools": self._tools()})
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
        if method == "POST" and path.startswith("/api/capabilities/"):
            return self._set_capability(path.rsplit("/", 1)[-1], body)
        if method == "GET" and path == "/api/audit":
            tenant = query.get("tenant") or None
            limit = int(query.get("limit") or 50)
            return Resp(200, {"audit": self.agent.audit.query(tenant_id=tenant, limit=limit)})
        if method == "POST" and path == "/api/chat":
            return self._chat(body, user)

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
        out = [{"integration": s.integration, "label": s.label, "kind": "api",
                "configured": s.configured, "missing": s.missing, "fingerprints": s.fingerprints}
               for s in credentials.status()]
        # local (non-credential) integrations — Obsidian vault + Hermes Agent
        from ..core.memory import VaultStore
        from ..core.hermes_skills import HermesSkillsReader
        v = VaultStore()
        kb, mems = v.list_kb(), v.list_client_memories()
        out.append({"integration": "obsidian", "label": "Obsidian Vault", "kind": "local",
                    "configured": v.root.exists(),
                    "detail": (f"{len(kb)} KB docs · {len(mems)} client notebooks" if v.root.exists()
                               else "vault not created yet"),
                    "path": str(v.root)})
        h = HermesSkillsReader()
        out.append({"integration": "hermes", "label": "Hermes Agent", "kind": "local",
                    "configured": h.available,
                    "detail": (f"{len(h.list_skills())} learned skills" if h.available
                               else "not connected"),
                    "path": str(h.root)})
        return out

    def _memory(self, tenant: str) -> Resp:
        from ..core.memory import VaultStore
        v = VaultStore()
        text = "" if tenant in ("", "*") else v.read_memory(tenant)
        return Resp(200, {"tenant": tenant, "memory": text, "kb": v.list_kb(),
                          "clients": v.list_client_memories()})

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

    def _skills(self) -> dict:
        """Hermes' learned skills (read-only). Empty + available=false until Hermes runs."""
        from ..core.hermes_skills import HermesSkillsReader
        r = HermesSkillsReader()
        return {"available": r.available, "dir": str(r.root), "skills": r.list_skills()}

    def _integration_fields(self, name: str) -> Resp:
        """The credential fields for an integration (which are set, fingerprints) — never raw."""
        spec = credentials.SPECS.get(name)
        if spec is None:
            return Resp(404, {"error": f"unknown integration '{name}'"})
        from ..core.config import fingerprint, get_config
        cfg = get_config()
        fields = [{"key": k, "required": k in spec.required,
                   "set": cfg.present(k), "fingerprint": fingerprint(cfg.get(k)) if cfg.present(k) else None}
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
        tenant = body.get("tenant") or "*"
        ctx = ToolContext(tenant_id=tenant, actor=user,
                          allow_cloud=bool(body.get("allow_cloud")),
                          client_factory=get_client_factory())
        turn = self.agent.chat(ctx, message)
        return Resp(200, {
            "answer": turn.answer, "citations": turn.citations,
            "tool_events": turn.tool_events, "provider": turn.provider,
            "model": turn.model, "rounds": turn.rounds, "tenant": tenant,
        })
