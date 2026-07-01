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
from pathlib import Path
from typing import Any, Iterator, Optional

# The deployment's install dir (this repo's root) — resolved at runtime, never hardcoded, so the
# product is portable across install paths.
_APP_DIR = str(Path(__file__).resolve().parents[2])

from ..agent import Agent
from ..core import credentials
from ..core.config import get_config
from ..core.context import ToolContext
from ..core.dispatch import dispatch
from ..runtime import get_client_factory
from .auth import AuthStore, SessionSigner

SESSION_COOKIE = "mspai_session"
TRUST_COOKIE = "mspai_trust"        # MFA trusted-device cookie (D-87 follow-up)


@dataclass
class Resp:
    status: int
    payload: Any
    set_cookie: Optional[str] = None
    clear_cookie: bool = False
    set_trust: Optional[str] = None       # trusted-device cookie (MFA remember-device — D-87)
    set_trust_max_age: int = 0
    clear_trust: bool = False


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
        self._fleet_ttl = get_config().int("MSPAI_FLEET_TTL_SEC", 300)
        from ..core.adminshell import AdminShell
        self.shell = AdminShell()                 # admin-only terminal (D-21); gated + audited below
        self._teams_bridge = None                 # MS Teams webhook bridge (D-29); built on first use
        self._stops: dict[str, "threading.Event"] = {}   # in-flight chat turns the user can stop (D-45)
        self._stops_lock = threading.Lock()

    def handle(self, method: str, path: str, query: dict, body: dict,
               user: Optional[str], trust: Optional[str] = None) -> Resp:
        # public
        if method == "POST" and path == "/api/login":
            return self._login(body, trust)
        if method == "POST" and path == "/api/logout":
            # "until signed out" mode: signing out also drops this device's MFA trust
            clear_trust = bool(user) and self.auth.get_mfa_trust_days(user) == 0
            return Resp(200, {"ok": True}, clear_cookie=True, clear_trust=clear_trust)
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
        # ── opt-in MFA self-service (D-87) ──
        if method == "POST" and path == "/api/me/mfa/setup":
            return self._mfa_setup(user)
        if method == "POST" and path == "/api/me/mfa/enable":
            return self._mfa_enable(user, body)
        if method == "POST" and path == "/api/me/mfa/disable":
            return self._mfa_disable(user, body)
        if method == "POST" and path == "/api/me/mfa/trust":
            return self._mfa_trust(user, body)
        if method == "GET" and path == "/api/me/memory":
            return self._me_memory_get(user)
        if method == "POST" and path == "/api/me/memory":
            return self._me_memory_set(body, user)
        if method == "GET" and path == "/api/users":
            return self._require_admin(role) or Resp(200, {"users": self.auth.list_users()})
        if method == "POST" and path == "/api/users":
            return self._require_admin(role) or self._create_user(body, user)
        if (method == "POST" and path.startswith("/api/users/")
                and path.endswith("/mfa-reset")):                # admin lockout recovery (D-87)
            return self._require_admin(role) or self._mfa_reset(path.split("/")[3], user)
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
            from ..core import connector_grants
            return Resp(200, {"approvals": self.agent.approvals.list(query.get("status") or None),
                              "pending": self.agent.approvals.count_pending(),
                              "batch_grants": getattr(self.agent.gate, "list_batches",
                                                      lambda: [])(),
                              "connector_grants": connector_grants.list_all()})
        if method == "POST" and path == "/api/connector-grants/revoke":
            return self._require_admin(role) or self._revoke_connector_grant(user, body)
        if method == "POST" and path == "/api/approvals/batch/revoke":
            return self._require_admin(role) or self._revoke_batches(user, body)
        if method == "POST" and path.startswith("/api/approvals/") and path.endswith("/approve"):
            return self._require_admin(role) or self._approve(int(path.split("/")[3]), user, body)
        if method == "POST" and path.startswith("/api/approvals/") and path.endswith("/reject"):
            return self._require_admin(role) or self._reject(int(path.split("/")[3]), user, body)
        if method == "GET" and path == "/api/credvault":
            return self._require_admin(role) or Resp(200, self.agent.credvault.status())
        if method == "POST" and path == "/api/credvault/passphrase":
            return self._require_admin(role) or self._cv_passphrase(body, user)
        if method == "POST" and path == "/api/credvault/passphrase/change":
            return self._require_admin(role) or self._cv_change(body, user)
        if method == "POST" and path == "/api/credvault/unlock":
            return self._require_admin(role) or self._cv_unlock(body, user)
        if method == "POST" and path == "/api/credvault/lock":
            return self._require_admin(role) or self._cv_lock(user)
        if method == "POST" and path.startswith("/api/credvault/slots/") and len(path.split("/")) == 5:
            return self._require_admin(role) or self._cv_slot_set(path.split("/")[4], body, user)
        if method == "DELETE" and path.startswith("/api/credvault/slots/") and len(path.split("/")) == 5:
            return self._require_admin(role) or self._cv_slot_delete(path.split("/")[4], user)
        if method == "POST" and path == "/api/credvault/autounlock":
            return self._require_admin(role) or self._cv_autounlock(body, user)
        if method == "GET" and path.startswith("/api/clients/") and path.endswith("/credentials"):
            return self._require_admin(role) or self._cv_list(path.split("/")[3])
        if method == "POST" and path.startswith("/api/clients/") and path.endswith("/credentials"):
            return self._require_admin(role) or self._cv_upsert(path.split("/")[3], body, user)
        if method == "POST" and path.startswith("/api/clients/") and path.endswith("/test") \
                and "/credentials/" in path and len(path.split("/")) == 7:
            return self._require_admin(role) or self._cv_test(path.split("/")[3], path.split("/")[5], body)
        if method == "DELETE" and path.startswith("/api/clients/") and "/credentials/" in path \
                and len(path.split("/")) == 6:
            return self._require_admin(role) or self._cv_delete(path.split("/")[3], path.split("/")[5], user)
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
        # ── custom integrations (D-27) — owner-defined; admin-gated; audited ──
        if method == "POST" and path == "/api/integrations/custom":
            return self._require_admin(role) or self._custom_create(body, user)
        if method == "POST" and path.startswith("/api/integrations/custom/") and path.endswith("/rename"):
            return self._require_admin(role) or self._custom_rename(path.split("/")[4], body, user)
        if method == "POST" and path.startswith("/api/integrations/custom/") and path.endswith("/docs"):
            return self._require_admin(role) or self._custom_docs(path.split("/")[4], body, user)
        if method == "GET" and path.startswith("/api/integrations/custom/") and len(path.split("/")) == 5:
            return self._custom_get(path.split("/")[4])
        if method == "POST" and path.startswith("/api/integrations/custom/") and len(path.split("/")) == 5:
            return self._require_admin(role) or self._custom_update(path.split("/")[4], body, user)
        if method == "DELETE" and path.startswith("/api/integrations/custom/") and len(path.split("/")) == 5:
            return self._require_admin(role) or self._custom_delete(path.split("/")[4], user)
        # ── email (D-28) + teams (D-29) helpers ──
        if method == "POST" and path == "/api/integrations/email/test":
            return self._require_admin(role) or self._email_test(body, user)
        if method == "GET" and path == "/api/integrations/email/recipients":
            return self._require_admin(role) or self._email_recipients_get()
        if method == "POST" and path == "/api/integrations/email/recipients":
            return self._require_admin(role) or self._email_recipients_set(body, user)
        if method == "GET" and path == "/api/integrations/msteams/allowlist":
            return self._require_admin(role) or self._teams_allowlist_get()
        if method == "POST" and path == "/api/integrations/msteams/allowlist":
            return self._require_admin(role) or self._teams_allowlist_set(body, user)
        if method == "GET" and path == "/api/integrations/msteams/cert":
            return self._require_admin(role) or self._teams_cert_get()
        if method == "POST" and path == "/api/integrations/msteams/cert":
            return self._require_admin(role) or self._teams_cert_generate(user)
        if method == "DELETE" and path == "/api/integrations/msteams/cert":
            return self._require_admin(role) or self._teams_cert_delete(user)
        if method == "GET" and path.startswith("/api/integrations/") and path.endswith("/fields"):
            return self._integration_fields(path.split("/")[3])
        if method == "POST" and path.startswith("/api/integrations/") and path.endswith("/credentials"):
            return self._set_credentials(path.split("/")[3], body, user)
        if method == "POST" and path == "/api/integrations/openai_codex/oauth/start":
            return self._codex_oauth_start()
        if method == "POST" and path == "/api/integrations/openai_codex/oauth/poll":
            return self._codex_oauth_poll(body, user)
        if method == "POST" and path == "/api/integrations/m365/oauth/start":
            return self._require_admin(role) or self._m365_oauth_start(body)
        if method == "POST" and path == "/api/integrations/m365/oauth/poll":
            return self._require_admin(role) or self._m365_oauth_poll(body, user)
        if method == "GET" and path == "/api/integrations/m365/clients":
            return self._require_admin(role) or self._m365_clients()
        if method == "POST" and path == "/api/integrations/m365/renew":
            return self._require_admin(role) or self._m365_renew(body, user)
        if method == "DELETE" and path.startswith("/api/integrations/m365/clients/") \
                and len(path.split("/")) == 6:
            return self._require_admin(role) or self._m365_disconnect(path.split("/")[5], user)
        if method == "DELETE" and path.startswith("/api/integrations/m365/clients/") \
                and len(path.split("/")) == 7 and path.split("/")[6] in ("exo", "spo"):
            return self._require_admin(role) or self._m365_disconnect(path.split("/")[5], user,
                                                                      service=path.split("/")[6])
        if method == "POST" and path == "/api/integrations/gws/oauth/start":
            return self._require_admin(role) or self._gws_oauth_start(body)
        if method == "GET" and path == "/api/integrations/gws/clients":
            return self._require_admin(role) or self._gws_clients()
        if method == "POST" and path == "/api/integrations/gws/renew":
            return self._require_admin(role) or self._gws_renew(body, user)
        if method == "DELETE" and path.startswith("/api/integrations/gws/clients/") \
                and len(path.split("/")) == 6:
            return self._require_admin(role) or self._gws_disconnect(path.split("/")[5], user)
        if method == "GET" and path == "/api/capabilities":
            from ..core.tool_groups import GROUP_INFO
            return Resp(200, {"capabilities": self._tools(),     # tools carry their policy
                              "group_info": GROUP_INFO})
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
            return self._require_admin(role) or self._set_capability(path.rsplit("/", 1)[-1], body, user)
        if method == "GET" and path == "/api/audit":
            tenant = query.get("tenant") or None
            limit = int(query.get("limit") or 50)
            return Resp(200, {"audit": self.agent.audit.query(tenant_id=tenant, limit=limit)})
        if method == "POST" and path == "/api/chat":
            return self._chat(body, user)
        if method == "POST" and path == "/api/chat/stop":
            return self.stop_chat(body, user)
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
        if method == "POST" and path == "/api/system/restart":
            return self._require_admin(role) or self._system_restart(user)
        if method == "GET" and path == "/api/fleet":
            return Resp(200, self._fleet(query.get("tenant") or "*", user,
                                         force=query.get("refresh") == "1"))

        return Resp(404, {"error": f"no route {method} {path}"})

    # ── handlers ──
    def _login(self, body: dict, trust: Optional[str] = None) -> Resp:
        username = body.get("username", "")
        role = self.auth.verify_login(username, body.get("password", ""))
        if not role:
            return Resp(401, {"error": "invalid credentials"})
        set_trust = None
        set_trust_age = 0
        if self.auth.mfa_is_enabled(username):                 # opt-in second factor (D-87)
            if not self._device_trusted(username, trust):
                code = str(body.get("code") or body.get("mfa_code") or "").strip()
                if not code:
                    # password is right but MFA is on, device not trusted — ask for the code
                    return Resp(200, {"mfa_required": True})
                if not self.auth.verify_mfa(username, code):
                    # 200 (not 401) so the form re-prompts instead of the api() 401 bounce
                    return Resp(200, {"mfa_required": True, "invalid_code": True})
                # first sign-in on this device passed — optionally remember it
                if body.get("remember", True):
                    days = self.auth.get_mfa_trust_days(username)
                    set_trust_age = (days or 3650) * 86400      # 0 = until-signed-out (long cookie)
                    set_trust = self.signer.make_trust(username, set_trust_age,
                                                       self.auth.mfa_secret_tag(username))
        token = self.signer.make(username, self.ttl)
        return Resp(200, {"ok": True, "role": role}, set_cookie=token,
                    set_trust=set_trust, set_trust_max_age=set_trust_age)

    def _device_trusted(self, username: str, trust: Optional[str]) -> bool:
        """A valid trust cookie for THIS user, whose tag still matches the current MFA secret."""
        v = self.signer.verify_trust(trust)
        return bool(v and v[0] == username and v[1] == self.auth.mfa_secret_tag(username))

    def _tools(self) -> list[dict]:
        out = []
        for t in self.agent.registry.all():
            pol = self.agent.caps.get(t.name, default_enabled=t.enabled_by_default)
            out.append({
                "name": t.name, "description": t.description, "source": t.source,
                "category": t.category, "risk": t.risk_level, "group": t.group,
                "enabled": self.agent.audit.is_enabled(t.name, t.enabled_by_default),
                "allow_write": pol.allow_write, "require_approval": pol.require_approval,
            })
        return out

    def _integrations(self) -> list[dict]:
        out = [{"integration": s.integration, "label": s.label,
                "kind": ("llm" if s.group == "llm" else
                         "custom" if s.group == "custom" else "api"),
                "group": s.group,
                "configured": s.configured, "missing": s.missing, "fingerprints": s.fingerprints}
               for s in credentials.status()]
        # enrich custom cards with their metadata (base URL, scopes, docs) — never secrets
        try:
            from ..core.custom_integrations import get_store
            recs = {ci.id: ci for ci in get_store().all()}
            for o in out:
                ci = recs.get(o["integration"])
                if ci is not None:
                    o.update({"base_url": ci.base_url, "read_paths": ci.read_paths,
                              "docs_url": ci.docs_url, "notes": ci.notes,
                              "auth_kind": ci.auth_kind})
        except Exception:
            pass
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

    @staticmethod
    def _approval_summary(tool: str, env: dict) -> str:
        """A chat-ready result line for an executed approval (D-47) — deterministic, no LLM call."""
        if not env.get("ok"):
            return f"⚠️ **{tool}** was approved but failed: {env.get('error') or 'unknown error'}"
        data = env.get("data")
        lines = [f"✅ Approved — ran **{tool}**."]
        if isinstance(data, dict):
            for k, v in data.items():
                if k in ("ok", "note") or v in (None, "", [], {}):
                    continue
                lines.append(f"- **{k.replace('_', ' ')}:** {v}")
            if data.get("note"):
                lines.append(f"_{data['note']}_")
        return "  \n".join(lines)

    def _run_approval(self, approval_id: int, user: str, body: Optional[dict]):
        """Claim + EXECUTE a pending action exactly as proposed (args-bound, one-shot); record the
        result, summarize it, and arm any batch grant (D-59). Shared by the JSON path (_approve —
        bell / Teams / API) and the inline streaming path (stream_approval). Returns a 4-tuple
        (err, env, row, summary): `err` is a Resp to return early (not found / not pending / lost
        the claim) else None; otherwise `env`/`row`/`summary` describe the executed action."""
        from ..core.gates import AlwaysApprove
        from ..runtime import get_client_factory
        row = self.agent.approvals.get(approval_id)
        if not row:
            return Resp(404, {"error": "approval not found"}), None, None, None
        if row["status"] != "pending":
            return Resp(409, {"error": f"already {row['status']}"}), None, row, None
        if not self.agent.approvals.claim_for_execution(approval_id, by=user):
            return Resp(409, {"error": "already decided"}), None, row, None
        ctx = ToolContext(tenant_id=row["tenant_id"], actor=f"{user} (approval#{approval_id})",
                          client_factory=get_client_factory())
        env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx,
                       name=row["tool"], args=row["args"], gate=AlwaysApprove())
        self.agent.approvals.mark_result(approval_id, bool(env["ok"]))
        self.agent.audit.record(actor=user, tenant_id=row["tenant_id"], action="approval_executed",
                                tool=row["tool"], category=row["category"], result_ok=bool(env["ok"]),
                                detail=f"approval#{approval_id}")
        summary = self._approval_summary(row["tool"], env)
        # D-59: "Approve + repeats" arms a bounded batch grant — same tool + same client only,
        # count-capped + 15-min TTL, never destructive, and only when this first run SUCCEEDED.
        if (body or {}).get("batch"):
            if not env["ok"]:
                summary += "\n🔁 _No auto-approval armed — the first run failed, so repeats " \
                           "still need individual approval._"
            else:
                g = self.agent.gate.grant_batch(
                    row["tenant_id"], row["tool"],
                    count=(body or {}).get("batch_count"), approval_id=approval_id, by=user) \
                    if hasattr(self.agent.gate, "grant_batch") else None
                if g:
                    self.agent.audit.record(
                        actor=user, tenant_id=row["tenant_id"], action="approval_batch_granted",
                        tool=row["tool"], category=row["category"], result_ok=True,
                        detail=f"approval#{approval_id} × {g['granted']} repeats, 15 min")
                    summary += (f"\n🔁 _Auto-approval armed: the next {g['granted']} "
                                f"`{row['tool']}` runs for **{row['tenant_id']}** go without "
                                f"asking (15 min, revocable from the bell panel)._")
                else:
                    summary += "\n⚠️ _Destructive actions can never be batch-approved — " \
                               "each run needs its own sign-off._"
        return None, env, row, summary

    def _resolve_conv(self, user: str, body: Optional[dict], row: dict) -> str:
        """The conversation to post the result + run the continuation into: the one the caller
        named (the inline chat card) else the one stored ON the approval row (the bell carries no
        conversation in its request — D-62 follow-up). Empty unless the caller actually owns it."""
        conv_id = str((body or {}).get("conversation_id") or "").strip() \
            or str(row.get("conversation_id") or "").strip()
        return conv_id if conv_id and self.agent.conversations.owns(user, conv_id) else ""

    def _approve(self, approval_id: int, user: str, body: Optional[dict] = None) -> Resp:
        """Approve + EXECUTE a pending action, once. When the originating conversation is known —
        passed in by the inline chat button OR stored on the approval row (so a BELL approval can
        resume too) — the result is posted back into that chat, the paused message's buttons are
        cleared, and the agent's continuation turn runs synchronously so the task picks back up
        (D-62). The inline chat UI prefers /api/approvals/stream, which streams that continuation
        live; this JSON path is the bell / Teams / API caller and runs it inline."""
        err, env, row, summary = self._run_approval(approval_id, user, body)
        if err:
            return err
        conv_id = self._resolve_conv(user, body, row)
        continuation = None
        if conv_id:
            self.agent.conversations.resolve_pending(user, conv_id, approval_id,
                                                     "executed" if env["ok"] else "failed")
            self.agent.conversations.add_message(user, conv_id, "assistant", summary,
                                                 meta={"approval_result": approval_id})
            # D-62: the paused turn promised "I'll continue as soon as you decide" — keep it.
            # Best-effort: the deterministic summary above already told the owner what ran, so
            # a continuation failure may never mask the executed action.
            try:
                continuation = self._continue_after_approval(user, conv_id, row, env)
            except Exception as e:                     # noqa: BLE001
                self.agent.audit.record(actor=user, tenant_id=row["tenant_id"],
                                        action="approval_continuation_failed", tool=row["tool"],
                                        result_ok=False, detail=str(e)[:200])
        return Resp(200, {"ok": True, "executed": env["ok"], "result": env,
                          "message": summary, "continuation": continuation})

    def stream_approval(self, body: dict, user: str) -> Iterator[dict]:
        """SSE: approve a pending action, then STREAM the agent's continuation turn live (D-62
        follow-up). The approve POST no longer blocks until the whole multi-round continuation
        finishes — the owner watches tool calls / reasoning / answer tokens arrive, just like a
        normal chat turn, instead of staring at a frozen 'Running…' button. The continuation is
        stoppable via /api/chat/stop (same _stops registry, keyed by conversation).
        Event types: decided · tool_call · tool_result · thinking · delta · answer · error.
        A `decision:"reject"` body skips the action and streams the continuation instead (D-103)."""
        approval_id = int((body or {}).get("approval_id") or 0)
        if str((body or {}).get("decision") or "approve").strip().lower() == "reject":
            yield from self._stream_reject(approval_id, user, body)
            return
        err, env, row, summary = self._run_approval(approval_id, user, body)
        if err:
            yield {"type": "error", "error": (err.payload or {}).get("error", "approval failed")}
            return
        yield {"type": "decided", "executed": bool(env["ok"]), "message": summary,
               "approval_id": approval_id, "tool": row["tool"]}
        conv_id = self._resolve_conv(user, body, row)
        if not conv_id:
            return                                  # no resumable chat (e.g. API caller) — done
        self.agent.conversations.resolve_pending(user, conv_id, approval_id,
                                                 "executed" if env["ok"] else "failed")
        self.agent.conversations.add_message(user, conv_id, "assistant", summary,
                                             meta={"approval_result": approval_id})
        yield from self._stream_continuation(user, conv_id, row, env)

    def _stream_reject(self, approval_id: int, user: str, body: dict) -> Iterator[dict]:
        """Reject a pending action, then STREAM the continuation so the task carries on past the
        skipped step (D-103) — the streamed twin of _reject, mirroring stream_approval's shape."""
        row = self.agent.approvals.get(approval_id)
        if not self.agent.approvals.reject(approval_id, by=user):
            yield {"type": "error", "error": "not pending"}
            return
        self.agent.audit.record(actor=user, tenant_id=(row or {}).get("tenant_id") or "*",
                                action="approval_rejected", detail=f"approval#{approval_id}")
        msg = "🚫 Rejected — skipping that action and continuing with the rest of the task."
        yield {"type": "decided", "executed": False, "rejected": True, "message": msg,
               "approval_id": approval_id, "tool": (row or {}).get("tool")}
        conv_id = self._resolve_conv(user, body, row or {})
        if not conv_id or not row:
            return                                  # no resumable chat (e.g. API caller) — done
        self.agent.conversations.resolve_pending(user, conv_id, approval_id, "rejected")
        self.agent.conversations.add_message(user, conv_id, "assistant", msg,
                                             meta={"approval_result": approval_id})
        yield from self._stream_continuation(user, conv_id, row, {"ok": False, "rejected": True},
                                             note=self._rejection_note(row))

    def _stream_continuation(self, user: str, conv_id: str, row: dict, env: dict,
                             *, note: Optional[str] = None) -> Iterator[dict]:
        """Stream the post-decision continuation turn (D-62) over SSE, mirroring stream_chat's
        push→pull queue+worker bridge so the agent's progress flows in real time. `note` overrides
        the synthetic instruction — used to drive the REJECT path ("skip this, continue the rest",
        D-103) instead of the default approved-and-ran note."""
        import json as _json
        from ..runtime import get_client_factory
        convs = self.agent.conversations
        conv = convs.get(user, conv_id)
        if not conv:
            return
        tenant = conv.get("tenant_id") or conv.get("tenant") or row["tenant_id"]
        model_id = self._conv_model_id(conv)
        ctx = ToolContext(tenant_id=tenant, actor=f"{user} (chat)",
                          allow_cloud=bool(model_id and not model_id.startswith("ollama:")),
                          client_factory=get_client_factory(),
                          _meta={"tasks": self.agent.tasks, "credvault": self.agent.credvault,
                                 "user_profile": self._user_profile(user),
                                 "conversation_id": conv_id})
        history = convs.history(user, conv_id)
        synthetic = note or self._continuation_note(row, env)
        q: "queue.Queue" = queue.Queue()
        DONE = object()
        result: dict = {}
        stop_ev = threading.Event()
        with self._stops_lock:                       # let POST /api/chat/stop reach the continuation
            self._stops[conv_id] = stop_ev

        def run():
            try:
                result["turn"] = self.agent.chat_stream(
                    ctx, synthetic, lambda e: q.put(e), model_id=model_id, history=history,
                    should_stop=stop_ev.is_set)
            except Exception as e:                   # contained; surfaced as an SSE error frame
                result["error"] = f"{type(e).__name__}: {e}"
                import sys, traceback                 # log the FULL trace to journald (D-95)
                print(f"[continuation] turn failed for {user}/{conv_id}: {result['error']}\n"
                      + traceback.format_exc(), file=sys.stderr, flush=True)
            finally:
                q.put(DONE)

        threading.Thread(target=run, daemon=True).start()
        try:
            while True:
                ev = q.get()
                if ev is DONE:
                    break
                yield ev
        finally:
            with self._stops_lock:
                self._stops.pop(conv_id, None)

        if "error" in result:
            # The deterministic summary already told the owner what ran (D-62), so a broken
            # follow-up never masks the executed action — surface a soft note and stop.
            self.agent.audit.record(actor=user, tenant_id=tenant,
                                    action="approval_continuation_failed", tool=row["tool"],
                                    result_ok=False, detail=str(result["error"])[:200])
            yield {"type": "answer", "answer": "", "pending": None,
                   "continuation_error": str(result["error"])[:200]}
            return
        turn = result["turn"]
        convs.add_message(user, conv_id, "assistant", turn.answer, meta={
            "tools": turn.tool_events, "citations": turn.citations, "pending": turn.pending,
            "reasoning": turn.reasoning or None, "stopped": turn.stopped,
            "label": f"{turn.provider}/{turn.model} · {turn.rounds} round(s)"})
        yield {"type": "answer", "answer": turn.answer, "citations": turn.citations,
               "tool_events": turn.tool_events, "provider": turn.provider, "model": turn.model,
               "rounds": turn.rounds, "reasoning": turn.reasoning or None,
               "stopped": turn.stopped, "pending": turn.pending, "conversation_id": conv_id}

    @staticmethod
    def _continuation_note(row: dict, env: dict) -> str:
        """The synthetic instruction that drives the post-approval continuation turn (D-62).

        Hard lesson (D-92): the old wording ("perform any remaining steps, and give a short status
        reply") let the model NARRATE the next step — e.g. claim it "submitted dtmaz2 for approval"
        — without ever calling the tool. No tool call → no approval row → no card, so a multi-target
        task silently stalled after the first target. This version forces ACTION over narration and
        forbids fake "submitted/pending/done" claims for anything it didn't actually call."""
        import json as _json
        blob = _json.dumps({k: env.get(k) for k in ("ok", "data", "error")}, default=str)[:4000]
        return (f"[system note — not from the owner] You APPROVED and ALREADY RAN the "
                f"'{row['tool']}' action; result: {blob}. Now CONTINUE the original task to "
                f"completion. If any steps remain — INCLUDING the same action for OTHER targets "
                f"the owner named (e.g. a second user/mailbox), or required follow-on changes — "
                f"actually CALL the necessary tool NOW; do not merely describe the next step. "
                f"Approval is automatic: calling a write tool surfaces its own approval card, so "
                f"NEVER say something is 'submitted', 'pending approval', 'queued', or 'done' "
                f"unless you actually called that tool THIS turn and saw its result. Do NOT re-run "
                f"'{row['tool']}' with the same arguments. Only when NO steps remain, give the "
                f"owner a short status reply.")

    @staticmethod
    def _rejection_note(row: dict) -> str:
        """Synthetic instruction for the continuation turn AFTER the owner REJECTED an action
        (D-103). Rejecting one proposed write does NOT cancel the task — the owner is curating a
        multi-step plan ("reject this target, do the next"). So: don't retry the rejected action,
        don't re-propose it, but DO carry on with every remaining step by actually calling the next
        tool (each surfaces its own card)."""
        return (f"[system note — not from the owner] The owner REVIEWED and REJECTED the proposed "
                f"'{row['tool']}' action — by deliberate choice it did NOT run. Do NOT retry it and "
                f"do NOT propose that SAME action again (same tool + same arguments). Rejecting ONE "
                f"action does NOT cancel the task: CONTINUE with the REMAINING steps the owner asked "
                f"for — including the same KIND of action for OTHER targets (e.g. the next group, "
                f"user, or mailbox) — by actually CALLING the next tool NOW; do not merely describe "
                f"it. Approval is automatic — calling a write tool surfaces its own approval card — "
                f"so NEVER say something is 'submitted', 'pending', 'queued', or 'done' unless you "
                f"actually called that tool THIS turn and saw its result. When no steps remain, give "
                f"the owner a short status that notes which action was skipped.")

    def _conv_model_id(self, conv: dict) -> Optional[str]:
        """The model the conversation last ran on — parsed from the stored 'provider/model ·'
        label so a gpt-5.5 chat CONTINUES on gpt-5.5 instead of falling to the local default."""
        import re
        for m in reversed(conv.get("messages") or []):
            label = str(((m.get("meta") or {}).get("label")) or "")
            hit = re.match(r"^([\w.-]+)/(\S+) ·", label)
            if hit and hit.group(1) != "mock":
                return f"{hit.group(1)}:{hit.group(2)}"
        return None

    def _continue_after_approval(self, user: str, conv_id: str, row: dict,
                                 env: dict, *, note: Optional[str] = None) -> Optional[dict]:
        """Run the agent's continuation turn after a decision (D-62). `note` overrides the synthetic
        instruction — the REJECT path passes _rejection_note so the task continues past a skipped
        action (D-103). Non-streamed (bell / Teams / API); the inline chat uses the streamed twin."""
        import json as _json
        from ..runtime import get_client_factory
        convs = self.agent.conversations
        conv = convs.get(user, conv_id)
        if not conv:
            return None
        tenant = conv.get("tenant_id") or conv.get("tenant") or row["tenant_id"]
        model_id = self._conv_model_id(conv)
        ctx = ToolContext(tenant_id=tenant, actor=f"{user} (chat)",
                          allow_cloud=bool(model_id and not model_id.startswith("ollama:")),
                          client_factory=get_client_factory(),
                          _meta={"tasks": self.agent.tasks, "credvault": self.agent.credvault,
                                 "user_profile": self._user_profile(user),
                                 "conversation_id": conv_id})
        history = convs.history(user, conv_id)
        synthetic = note or self._continuation_note(row, env)
        turn = self.agent.chat(ctx, synthetic, model_id=model_id, history=history)
        if not (turn.answer or turn.pending):
            return None
        convs.add_message(user, conv_id, "assistant", turn.answer, meta={
            "tools": turn.tool_events, "citations": turn.citations, "pending": turn.pending,
            "reasoning": turn.reasoning or None,
            "label": f"{turn.provider}/{turn.model} · {turn.rounds} round(s)"})
        return {"message": turn.answer, "pending": turn.pending,
                "tools": turn.tool_events, "citations": turn.citations,
                "reasoning": turn.reasoning or None,
                "label": f"{turn.provider}/{turn.model} · {turn.rounds} round(s)"}

    def _revoke_batches(self, user: str, body: Optional[dict] = None) -> Resp:
        """Kill active batch grants (D-59) — all of them, or one (tenant, tool) pair."""
        n = self.agent.gate.revoke_batches((body or {}).get("tenant_id") or None,
                                           (body or {}).get("tool") or None) \
            if hasattr(self.agent.gate, "revoke_batches") else 0
        self.agent.audit.record(actor=user, tenant_id=(body or {}).get("tenant_id") or "*",
                                action="approval_batch_revoked", result_ok=True,
                                detail=f"{n} grant(s) revoked")
        return Resp(200, {"ok": True, "revoked": n})

    def _revoke_connector_grant(self, user: str, body: Optional[dict] = None) -> Resp:
        """Remove an owner-approved connector cmdlet grant (D-64) — the off switch."""
        from ..core import connector_grants
        connector = str((body or {}).get("connector") or "").strip()
        cmdlet = str((body or {}).get("cmdlet") or "").strip()
        gone = connector_grants.revoke(connector, cmdlet) if (connector and cmdlet) else False
        if gone:
            self.agent.audit.record(actor=user, tenant_id="*",
                                    action="connector_grant_revoked", tool=cmdlet,
                                    result_ok=True, detail=f"{connector}:{cmdlet}")
        return Resp(200, {"ok": True, "revoked": gone})

    def _reject(self, approval_id: int, user: str, body: Optional[dict] = None) -> Resp:
        row = self.agent.approvals.get(approval_id)
        if not self.agent.approvals.reject(approval_id, by=user):
            return Resp(409, {"error": "not pending"})
        self.agent.audit.record(actor=user, tenant_id="*", action="approval_rejected",
                                detail=f"approval#{approval_id}")
        msg = "🚫 Rejected — skipping that action and continuing with the rest of the task."
        # Post the note into the originating chat — named by the inline card, else stored on the
        # row so a BELL rejection lands in the right thread too (D-62 follow-up). Rejecting ONE
        # action no longer ends the task: run the continuation so the agent moves to the next step
        # (D-103). Best-effort — a continuation failure never masks that the action was rejected.
        conv_id = self._resolve_conv(user, body, row or {})
        continuation = None
        if conv_id and row:
            self.agent.conversations.resolve_pending(user, conv_id, approval_id, "rejected")
            self.agent.conversations.add_message(user, conv_id, "assistant", msg,
                                                 meta={"approval_result": approval_id})
            try:
                continuation = self._continue_after_approval(
                    user, conv_id, row, {"ok": False, "rejected": True},
                    note=self._rejection_note(row))
            except Exception as e:                     # noqa: BLE001
                self.agent.audit.record(actor=user, tenant_id=row["tenant_id"],
                                        action="approval_continuation_failed", tool=row["tool"],
                                        result_ok=False, detail=str(e)[:200])
        return Resp(200, {"ok": True, "message": msg, "continuation": continuation})

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

    def _system_restart(self, user: str) -> Resp:
        """Restart the msp-ai service (admin Terminal button, D-36). Audited BEFORE it runs — this
        process is about to be killed. Launched via systemd-run so PID1 owns the restart and it
        survives this process dying; falls back to plain systemctl if systemd-run is absent."""
        import shutil
        import subprocess
        self.agent.audit.record(actor=user, tenant_id="*", action="terminal",
                                tool="systemctl", detail="restart msp-ai (Terminal button)")
        unit = get_config().get("MSPAI_SERVICE_UNIT") or "msp-ai"
        cmd = (["sudo", "-n", "systemd-run", "--collect", "systemctl", "restart", unit]
               if shutil.which("systemd-run")
               else ["sudo", "-n", "systemctl", "restart", unit])
        try:
            subprocess.Popen(cmd, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:                       # noqa: BLE001
            return Resp(500, {"error": f"could not launch restart: {e}"})
        return Resp(200, {"ok": True, "restarting": True, "unit": unit})

    def _terminal_state(self, user: str) -> Resp:
        """Initial state for the admin Terminal tab: enabled flag, working dir, run-as user + host."""
        import getpass
        import socket
        from ..core.adminshell import terminal_enabled
        en = terminal_enabled()
        try:
            who, host = getpass.getuser(), socket.gethostname()
        except OSError:
            who, host = "msp-ai", "server"
        return Resp(200, {"enabled": en, "cwd": self.shell.cwd(user) if en else None,
                          "user": who, "host": host})

    def _terminal_run(self, body: dict, user: str) -> Resp:
        """Run one shell command as the service user (admin-only, D-21). AUDITED BEFORE it runs, so
        even a command that kills the process leaves a record. Returns stdout/stderr/exit/cwd."""
        from ..core.adminshell import terminal_enabled
        if not terminal_enabled():
            return Resp(403, {"error": "admin terminal is disabled (MSPAI_ADMIN_TERMINAL=0)"})
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

    # ── opt-in MFA (TOTP) self-service + admin reset (D-87) ──────────────────
    def _mfa_setup(self, user: str) -> Resp:
        """Generate a pending secret + otpauth URI to scan; MFA stays OFF until /enable confirms."""
        try:
            secret, uri = self.auth.start_mfa_setup(user)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        payload = {"secret": secret, "otpauth_uri": uri}
        try:                                          # render a scannable QR on-box (no external call)
            import segno
            payload["qr_svg"] = segno.make(uri, error="m").svg_data_uri(
                scale=5, border=3, light="#ffffff", dark="#0b1220")
        except Exception:                             # segno absent → UI falls back to the setup key
            pass
        return Resp(200, payload)

    def _mfa_enable(self, user: str, body: dict) -> Resp:
        if self.auth.confirm_mfa(user, str(body.get("code") or "")):
            self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                    tool=user, detail="mfa_enabled")
            return Resp(200, {"ok": True, "mfa_enabled": True})
        return Resp(400, {"error": "that code didn't match — make sure the time is correct and try "
                                   "the current 6-digit code"})

    def _mfa_disable(self, user: str, body: dict) -> Resp:
        # re-auth with a current code so a stolen session can't silently turn MFA off
        if self.auth.disable_mfa(user, code=str(body.get("code") or "")):
            self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                    tool=user, detail="mfa_disabled")
            return Resp(200, {"ok": True, "mfa_enabled": False})
        return Resp(400, {"error": "enter a current authentication code to turn MFA off"})

    def _mfa_trust(self, user: str, body: dict) -> Resp:
        """Set how long a device stays trusted after an MFA sign-in: 0/30/60/90 (0 = until sign-out)."""
        try:
            self.auth.set_mfa_trust_days(user, int(body.get("days", 30)))
        except (ValueError, TypeError) as e:
            return Resp(400, {"error": str(e)})
        return Resp(200, {"ok": True, "mfa_trust_days": self.auth.get_mfa_trust_days(user)})

    def _mfa_reset(self, name: str, actor: str) -> Resp:
        """Admin clears a user's MFA so a user who lost their authenticator can get back in."""
        if not self.auth.get_user(name):
            return Resp(404, {"error": f"user '{name}' not found"})
        self.auth.disable_mfa(name, admin=True)
        self.agent.audit.record(actor=actor, tenant_id="*", action="config_change",
                                tool=name, detail=f"mfa_reset={name}")
        return Resp(200, {"ok": True, "users": self.auth.list_users()})

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
                created_by=f"msp-ai:{user}", tenant=tenant,
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

    # ── credential vault (D-25/D-30) — admin-only; per-admin slots; mutations + unlock audited ──
    def _cv_passphrase(self, body: dict, user: str) -> Resp:
        try:
            self.agent.credvault.set_passphrase(body.get("passphrase") or "", username=user)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change", detail="credvault_init")
        return Resp(200, self.agent.credvault.status())

    def _cv_change(self, body: dict, user: str) -> Resp:
        try:
            self.agent.credvault.change_passphrase(body.get("old") or "", body.get("new") or "",
                                                   username=user)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail="credvault_passphrase_change")
        return Resp(200, self.agent.credvault.status())

    def _cv_unlock(self, body: dict, user: str) -> Resp:
        try:
            ok = self.agent.credvault.unlock(body.get("passphrase") or "", username=user)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="credential_view",
                                detail=f"credvault_unlock ok={ok}", result_ok=ok)
        if not ok:
            return Resp(401, {"error": "incorrect passphrase"})
        # Vault just opened: sweep any inline-fallback M365/EXO tokens (connected while it was
        # locked, D-37/D-41) into the encrypted credentials file now, not on their next use.
        try:
            from ..core import m365_auth
            from ..core.config import get_config
            swept = m365_auth.migrate_inline_secrets(get_config())
            if swept.get("moved"):
                self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                        detail=f"m365 tokens migrated to vault: {swept['moved']}")
        except Exception:                            # never let a sweep failure break unlock
            pass
        return Resp(200, self.agent.credvault.status())

    def _cv_slot_set(self, target: str, body: dict, user: str) -> Resp:
        """Set/reset an admin's vault passphrase (D-30). Vault must be unlocked; the target must
        be an admin account — this is the lost-passphrase recovery path."""
        from ..core.credvault import VaultLocked
        if self.auth.get_role(target) != "admin":
            return Resp(400, {"error": f"'{target}' is not an admin user"})
        try:
            r = self.agent.credvault.set_slot(target, body.get("passphrase") or "", by=user)
        except VaultLocked as e:
            return Resp(423, {"error": f"{e} — unlock with YOUR passphrase first", "locked": True})
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"credvault_slot_set={target}"
                                       f"{' (self)' if target == user else ''}")
        return Resp(200, {**r, "status": self.agent.credvault.status()})

    def _cv_slot_delete(self, target: str, user: str) -> Resp:
        from ..core.credvault import VaultLocked
        try:
            r = self.agent.credvault.delete_slot(target)
        except VaultLocked as e:
            return Resp(423, {"error": str(e), "locked": True})
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"credvault_slot_delete={target}")
        return Resp(200, {**r, "status": self.agent.credvault.status()})

    def _cv_autounlock(self, body: dict, user: str) -> Resp:
        """Owner toggle (D-30): let the AGENT use the vault unattended (auto-unlock on demand)."""
        from ..core.credvault import VaultLocked
        enabled = bool(body.get("enabled"))
        try:
            self.agent.credvault.set_service_unlock(enabled)
        except VaultLocked as e:
            return Resp(423, {"error": f"{e} — unlock first to enable auto-unlock", "locked": True})
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"credvault_autounlock={'on' if enabled else 'off'}")
        return Resp(200, self.agent.credvault.status())

    def _cv_lock(self, user: str) -> Resp:
        self.agent.credvault.lock()
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change", detail="credvault_lock")
        return Resp(200, self.agent.credvault.status())

    def _cv_list(self, tenant: str) -> Resp:
        from ..core.credvault import VaultLocked
        try:
            return Resp(200, {"tenant": tenant, "credentials": self.agent.credvault.admin_list(tenant)})
        except VaultLocked as e:
            return Resp(423, {"error": str(e), "locked": True})

    def _cv_upsert(self, tenant: str, body: dict, user: str) -> Resp:
        from ..core.credvault import VaultLocked
        fields = body.get("fields")
        if not isinstance(fields, dict):
            return Resp(400, {"error": "fields object required"})
        try:
            r = self.agent.credvault.upsert(tenant, body.get("label") or "", fields,
                                            notes=body.get("notes") or "", actor=user)
        except VaultLocked as e:
            return Resp(423, {"error": str(e), "locked": True})
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id=tenant, action="config_change",
                                detail=f"credential_set={r['label']}")   # never the value
        return Resp(200, r)

    def _cv_delete(self, tenant: str, label: str, user: str) -> Resp:
        from ..core.credvault import VaultLocked
        try:
            r = self.agent.credvault.delete(tenant, label)
        except VaultLocked as e:
            return Resp(423, {"error": str(e), "locked": True})
        except ValueError as e:
            return Resp(404, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id=tenant, action="config_change",
                                detail=f"credential_delete={label}")
        return Resp(200, r)

    def _cv_test(self, tenant: str, label: str, body: dict) -> Resp:
        """Owner 'does the append work' check — reports a fingerprint, never the value."""
        from ..core.credvault import AppendRequired, VaultLocked
        try:
            return Resp(200, self.agent.credvault.test_assemble(
                tenant, label, start=body.get("start") or "", end=body.get("end") or ""))
        except AppendRequired as e:
            return Resp(200, {"ok": False, "append_required": e.need, "label": label})
        except VaultLocked as e:
            return Resp(423, {"error": str(e), "locked": True})
        except ValueError as e:
            return Resp(404, {"error": str(e)})

    # ── branding — owner logo for the sidebar + login screen (admin-managed, publicly visible) ──
    _LOGO_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")
    _LOGO_MAX = 2 * 1024 * 1024

    def _branding_dir(self):
        from pathlib import Path
        d = get_config().get("MSPAI_BRANDING_DIR")    # test override; default = served /vendor dir
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

    _LOGO_FIT = 512                       # raster logos are downscaled to fit this box

    @classmethod
    def _fit_logo(cls, data: bytes, ext: str) -> tuple[bytes, str]:
        """Downscale a raster logo to ≤512px and normalize to PNG (keeps alpha, strips EXIF).
        SVG (vector) and GIF (animation) pass through; any failure keeps the original."""
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            return data, ext
        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "A" in img.getbands() or "P" in img.mode else "RGB")
            img.thumbnail((cls._LOGO_FIT, cls._LOGO_FIT))   # only ever shrinks; keeps aspect
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=True)
            return out.getvalue(), ".png"
        except Exception:
            return data, ext

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
        data, ext = self._fit_logo(data, ext)
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
    _FS_ROOTS = (_APP_DIR, "/home", "/srv", "/etc", "/var/log", "/")
    _FS_PREVIEW_MAX = 256 * 1024
    _FS_UPLOAD_MAX = 25 * 1024 * 1024

    @staticmethod
    def _fs_norm(p: str):
        import os
        from pathlib import Path
        return Path(os.path.realpath(p or "/"))

    def _fs_list(self, pathq: str) -> Resp:
        import stat as _stat
        p = self._fs_norm(pathq or _APP_DIR)
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
        if str(p) in ("/", _APP_DIR):
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
        try:
            path.with_suffix(".py.bak").write_text(prev, encoding="utf-8")
            path.write_text(code, encoding="utf-8")
        except OSError as e:
            return Resp(500, {"error": self._fs_error("edit", path, e)})
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
        try:
            new_path.write_text(src2, encoding="utf-8")
            old_path.unlink()
        except OSError as e:
            return Resp(500, {"error": self._fs_error("rename", old_path, e)})
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

    @staticmethod
    def _fs_error(action: str, path, e: OSError) -> str:
        """A clear, actionable message when a skill-file op is blocked — almost always the service
        user not owning the file (harness/root-created skills). Tells the owner exactly how to fix."""
        import errno
        if isinstance(e, PermissionError) or getattr(e, "errno", None) == errno.EACCES:
            return (f"can't {action} '{getattr(path, 'name', path)}' — the MSP AI service user "
                    f"doesn't own this skill file. Fix on the host: "
                    f"sudo chown -R msp-ai:msp-ai execution/skills skills_candidate .tmp")
        return f"can't {action} '{getattr(path, 'name', path)}': {e}"

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
        try:
            path.write_text(s2, encoding="utf-8")
        except OSError as e:
            return self._fs_error("move", path, e)
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
        try:
            trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(trash / f"{name}.py"))
        except OSError as e:
            return Resp(500, {"error": self._fs_error("delete", path, e)})
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
        spec = credentials.spec_for(name)
        if spec is None:
            return Resp(404, {"error": f"unknown integration '{name}'"})
        import os
        from ..core.config import fingerprint, get_config
        cfg = get_config()
        # custom integrations (D-27) carry the owner's field labels + secret flags
        labels: dict[str, str] = {}
        secrets: dict[str, bool] = {}
        if spec.group == "custom":
            from ..core.custom_integrations import get_store
            ci = get_store().get(name)
            if ci is not None:
                labels = {f["key"]: f["label"] for f in ci.fields}
                secrets = {f["key"]: bool(f.get("secret", True)) for f in ci.fields}
        # A non-empty value in the process env (e.g. systemd EnvironmentFile / .env) OUTRANKS the
        # SecretStore, so editing the key in the UI has no effect until that env value is removed.
        # Flag it so the dashboard can warn instead of silently ignoring the edit.
        fields = []
        for k in (*spec.required, *spec.optional):
            info = credentials.FIELD_INFO.get(k, {})
            is_secret = secrets.get(k, info.get("secret", True))
            row = {
                "key": k, "required": k in spec.required,
                "label": labels.get(k) or info.get("label"),
                "secret": is_secret,
                "help": info.get("help"), "placeholder": info.get("placeholder"),
                "hidden": bool(info.get("hidden")),
                "set": cfg.present(k),
                "fingerprint": fingerprint(cfg.get(k)) if cfg.present(k) else None,
                "shadowed": bool(os.environ.get(k))}
            # NON-secret config (URLs, scopes, tenant names…) is shown in full — the owner
            # must be able to READ settings, not just replace them blind. Secrets stay
            # fingerprint-only (I-3).
            if not is_secret and cfg.present(k):
                row["value"] = cfg.get(k)
            fields.append(row)
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

    def _codex_oauth_start(self) -> Resp:
        """Begin the 'Sign in with ChatGPT' device flow (D-26) — returns the link + one-time
        code the GUI shows. Nothing is stored until the poll completes."""
        from ..core import codex_auth
        try:
            return Resp(200, codex_auth.start_device_auth())
        except Exception as e:
            return Resp(502, {"error": f"could not start OpenAI sign-in: {e}"})

    def _codex_oauth_poll(self, body: dict, user: str) -> Resp:
        """One poll of the pending device sign-in; on approval, exchange + persist tokens."""
        device_auth_id = str((body or {}).get("device_auth_id") or "").strip()
        user_code = str((body or {}).get("user_code") or "").strip()
        if not device_auth_id or not user_code:
            return Resp(400, {"error": "device_auth_id and user_code required"})
        from ..core import codex_auth
        from ..core.config import fingerprint, get_config
        try:
            status, payload = codex_auth.poll_device_auth(device_auth_id, user_code)
        except Exception as e:
            return Resp(502, {"error": f"OpenAI sign-in poll failed: {e}"})
        if status == "pending":
            return Resp(200, {"status": "pending"})
        try:
            access, _acct = codex_auth.exchange_device_code(get_config(), payload)
        except Exception as e:
            return Resp(502, {"error": f"OpenAI token exchange failed: {e}"})
        self.agent.audit.record(actor=user, tenant_id="*", action="credential_set",
                                tool="openai_codex", detail="connected via ChatGPT sign-in (device flow)")
        return Resp(200, {"status": "connected",
                          "fingerprints": {codex_auth.ACCESS_KEY: fingerprint(access)}})

    # ── custom integrations (D-27) — owner-defined connections; metadata only, never secrets ──
    def _custom_get(self, cid: str) -> Resp:
        from ..core.custom_integrations import get_store
        ci = get_store().get(cid)
        if ci is None:
            return Resp(404, {"error": f"unknown custom integration '{cid}'"})
        return Resp(200, ci.to_dict())

    def _custom_create(self, body: dict, user: str) -> Resp:
        from ..core.custom_integrations import get_store
        try:
            ci = get_store().create(body or {})
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"integration_create={ci.id}")
        return Resp(200, ci.to_dict())

    def _custom_update(self, cid: str, body: dict, user: str) -> Resp:
        from ..core.custom_integrations import get_store
        try:
            ci = get_store().update(cid, body or {})
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        try:
            get_client_factory().invalidate(cid)   # auth placement/base URL may have changed
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"integration_update={cid}")
        return Resp(200, ci.to_dict())

    def _custom_rename(self, cid: str, body: dict, user: str) -> Resp:
        """Rename a custom integration's id — migrates its stored secret values server-side."""
        from ..core.config import get_config
        from ..core.custom_integrations import get_store
        new_id = (body.get("id") or "").strip().lower()
        try:
            ci, key_map = get_store().rename(cid, new_id)
        except ValueError as e:
            return Resp(400, {"error": str(e)})
        cfg = get_config()
        store = getattr(cfg, "secrets", None)
        if store is not None and key_map:
            moves: dict[str, str] = {}
            for old_key, new_key in key_map.items():
                val = store.get(old_key)
                if val:
                    moves[new_key] = val
                    moves[old_key] = ""            # clear the old key
            if moves:
                store.set_many(moves, allowed_keys=set(moves))
        try:
            get_client_factory().invalidate(cid)
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"integration_rename={cid}->{ci.id}")
        return Resp(200, ci.to_dict())

    def _custom_delete(self, cid: str, user: str) -> Resp:
        """Delete a custom integration + clear its stored secrets (fail-closed afterwards)."""
        from ..core.config import get_config
        from ..core.custom_integrations import get_store
        try:
            ci = get_store().delete(cid)
        except ValueError as e:
            return Resp(404, {"error": str(e)})
        store = getattr(get_config(), "secrets", None)
        keys = [f["key"] for f in ci.fields]
        if store is not None and keys:
            try:
                store.set_many({k: "" for k in keys}, allowed_keys=set(keys))
            except Exception:
                pass
        try:
            get_client_factory().invalidate(cid)
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"integration_delete={cid}")
        return Resp(200, {"ok": True, "deleted": cid})

    def _custom_docs(self, cid: str, body: dict, user: str) -> Resp:
        """Fetch a docs URL (https-only, SSRF-guarded) into the KB for this integration."""
        from ..core.custom_integrations import get_store
        from ..core.docfetch import DocFetchError, fetch_text
        from ..core.memory import VaultStore
        ci = get_store().get(cid)
        if ci is None:
            return Resp(404, {"error": f"unknown custom integration '{cid}'"})
        url = (body.get("url") or "").strip()
        if not url:
            return Resp(400, {"error": "url required"})
        try:
            doc = fetch_text(url)
        except DocFetchError as e:
            return Resp(400, {"error": str(e)})
        import re as _re
        slug = _re.sub(r"[^a-z0-9]+", "-", (doc["title"] or url.split("/")[-1] or "doc").lower()).strip("-")[:60]
        content = (f"# {doc['title'] or ci.label} — vendor docs\n\n"
                   f"> Source: {doc['url']}  (ingested for integration `{ci.id}`)\n\n{doc['text']}\n")
        r = VaultStore().write_kb_doc(f"integrations/{ci.id}-{slug or 'doc'}", content)
        if r.get("error"):
            return Resp(400, r)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"integration_docs={cid}<-{url[:120]}")
        return Resp(200, {"ok": True, "doc": r["doc"], "title": doc["title"],
                          "chars": len(doc["text"])})

    # ── email (D-28) ──
    def _email_test(self, body: dict, user: str) -> Resp:
        """Send a real test email through the configured transport (admin; audited)."""
        try:
            client = get_client_factory()("email", "*")
        except credentials.MissingCredential as e:
            return Resp(400, {"error": str(e)})
        to = (body.get("to") or "").strip() or None
        r = client.send("MSP AI test email",
                        f"This is a test email from MSP AI, requested by {user}.", to=to)
        self.agent.audit.record(actor=user, tenant_id="*", action="tool_call", tool="send_email",
                                category="alert", result_ok=bool(r.get("ok")),
                                detail=f"email_test to={to or 'default'}")
        return Resp(200 if r.get("ok") else 400, r)

    # ── email recipient allowlist (D-28) — addresses are not secrets; managed as a list ──
    def _email_recipients_get(self) -> Resp:
        import re as _re
        from ..core.config import get_config
        cfg = get_config()
        raw = (cfg.get("EMAIL_ALLOWED_RECIPIENTS") or "").strip()
        default_to = (cfg.get("EMAIL_DEFAULT_TO") or "").strip()
        # Surface a malformed default recipient (e.g. a fat-fingered address) instead of letting
        # every to-less send fail-closed silently at the recipient floor.
        default_valid = bool(_re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", default_to)) if default_to \
            else None
        return Resp(200, {
            "allow_all": raw == "*",
            "entries": [] if raw == "*" else [e.strip() for e in raw.split(",") if e.strip()],
            "default_to": default_to,
            "default_to_valid": default_valid,
        })

    def _email_recipients_set(self, body: dict, user: str) -> Resp:
        import re as _re
        entries = body.get("entries")
        if not isinstance(entries, list):
            return Resp(400, {"error": "entries list required"})
        clean: list[str] = []
        for e in entries[:200]:
            e = str(e or "").strip().lower()
            if not e:
                continue
            if not _re.fullmatch(r"(@[a-z0-9.-]+\.[a-z]{2,}|[^@\s,|]+@[a-z0-9.-]+\.[a-z]{2,})", e):
                return Resp(400, {"error": f"'{e}' is not a valid address or @domain"})
            if e not in clean:
                clean.append(e)
        value = "*" if body.get("allow_all") else ",".join(clean)
        try:
            credentials.set_integration("email", {"EMAIL_ALLOWED_RECIPIENTS": value})
        except credentials.MissingCredential as e:
            return Resp(400, {"error": str(e)})
        try:
            get_client_factory().invalidate("email")     # floor changes take effect now
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"email_recipients ({'anyone' if value == '*' else f'{len(clean)} entr(ies)'})")
        return self._email_recipients_get()

    # ── teams allowlist (D-29) — ids are not secrets; managed as a list in the card UI ──
    def _teams_allowlist_get(self) -> Resp:
        from ..core.config import get_config
        from ..clients.msteams import parse_allowlist
        cfg = get_config()
        return Resp(200, {
            "entries": parse_allowlist(cfg.get("TEAMS_ALLOWED_USERS") or ""),
            "allow_all": str(cfg.get("TEAMS_ALLOW_ALL_USERS") or "").lower() in ("1", "true", "yes"),
            "webhook_path": "/api/teams/messages",
            "enabled": cfg.bool("MSPAI_TEAMS", True),
            "configured": credentials.is_configured("msteams", cfg),
        })

    def _teams_allowlist_set(self, body: dict, user: str) -> Resp:
        entries = body.get("entries")
        if not isinstance(entries, list):
            return Resp(400, {"error": "entries list required"})
        import re as _re
        parts = []
        for e in entries[:100]:
            uid = str((e or {}).get("id") or "").strip()
            name = str((e or {}).get("name") or "").strip().replace(",", " ").replace("|", " ")
            link = str((e or {}).get("user") or "").strip()
            if not uid or not _re.fullmatch(r"[A-Za-z0-9*\-]{1,64}", uid):
                return Resp(400, {"error": f"'{uid or '?'}' is not a valid AAD object id"})
            if link and self.auth.get_role(link) is None:
                return Resp(400, {"error": f"linked account '{link}' is not a dashboard user"})
            parts.append("|".join([uid, name, link]).rstrip("|"))
        values = {"TEAMS_ALLOWED_USERS": ",".join(parts)}
        if "allow_all" in body:
            values["TEAMS_ALLOW_ALL_USERS"] = "true" if body.get("allow_all") else ""
        try:
            credentials.set_integration("msteams", values)
        except credentials.MissingCredential as e:
            return Resp(400, {"error": str(e)})
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"teams_allowlist ({len(parts)} user(s)"
                                       f"{', allow_all' if body.get('allow_all') else ''})")
        return self._teams_allowlist_get()

    # ── teams app certificate (D-29 amendment) — private key never leaves the server ──
    def _teams_cert_get(self) -> Resp:
        from ..core import teams_cert
        from ..core.config import get_config
        i = teams_cert.info()
        if i.get("exists"):
            i["public_pem"] = teams_cert.public_pem()
        i["secret_set"] = get_config().present("TEAMS_CLIENT_SECRET")
        i["auth_method"] = ("certificate" if i.get("exists")
                            else "client_secret" if i["secret_set"] else "none")
        return Resp(200, i)

    def _teams_cert_generate(self, user: str) -> Resp:
        from ..core import teams_cert
        try:
            r = teams_cert.generate()
        except Exception as e:
            return Resp(500, {"error": f"certificate generation failed: {e}"})
        try:
            get_client_factory().invalidate("msteams")   # switch token flow immediately
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail=f"teams_cert_generate thumbprint={r.get('thumbprint','?')[:16]}")
        return Resp(200, r)

    def _teams_cert_delete(self, user: str) -> Resp:
        from ..core import teams_cert
        ok = teams_cert.delete()
        if not ok:
            return Resp(404, {"error": "no certificate to delete"})
        try:
            get_client_factory().invalidate("msteams")
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail="teams_cert_delete")
        return Resp(200, {"ok": True})

    # ── teams webhook (D-29) — called by server.py WITHOUT a session cookie; the Bot
    # Framework JWT is the authentication. All checks live in TeamsBridge (fail-closed). ──
    def teams_webhook(self, auth_header: str, body: dict) -> Resp:
        if self._teams_bridge is None:
            from ..core.teams_bot import TeamsBridge
            self._teams_bridge = TeamsBridge(self.agent, user_lookup=self.auth.get_user)
        status, payload = self._teams_bridge.handle(auth_header, body)
        return Resp(status, payload)

    # ── Microsoft 365 per-client device-code sign-in (D-32/D-33) — password + MFA at Microsoft ──
    def _m365_clients(self) -> Resp:
        """Per-client M365 connection status for the card."""
        from ..core import m365_auth
        from ..core.config import get_config
        from ..core.memory import VaultStore
        cfg = get_config()
        connected = set(m365_auth.list_connected(cfg))
        exo_connected = set(m365_auth.list_connected(cfg, service="exo"))
        spo_connected = set(m365_auth.list_connected(cfg, service="spo"))
        clients = []
        for c in VaultStore().list_clients():
            row = {"tenant": c, "connected": c in connected,
                   "fingerprint": m365_auth.fingerprint_for(cfg, c) if c in connected else None}
            if c in connected:
                row.update(m365_auth.health(cfg, c))
            exo = {"connected": c in exo_connected}     # Exchange connection (D-41), per client
            if exo["connected"]:
                exo.update(m365_auth.health(cfg, c, service="exo"))
                exo["fingerprint"] = m365_auth.fingerprint_for(cfg, c, service="exo")
            row["exo"] = exo
            spo = {"connected": c in spo_connected}     # SharePoint connection (D-89), per client
            if spo["connected"]:
                spo.update(m365_auth.health(cfg, c, service="spo"))
                spo["fingerprint"] = m365_auth.fingerprint_for(cfg, c, service="spo")
            row["spo"] = spo
            clients.append(row)
        # No registration needed: a built-in Microsoft app is always available (D-34).
        return Resp(200, {"app_configured": True, "using_builtin": not cfg.present("M365_CLIENT_ID"),
                          "renew_hours": cfg.int("MSPAI_M365_RENEW_HOURS", 12), "clients": clients})

    def _m365_oauth_start(self, body: dict) -> Resp:
        from ..core import m365_auth
        from ..core.config import get_config
        tenant = str((body or {}).get("tenant") or "").strip()
        service = str((body or {}).get("service") or "m365").strip()
        if service not in ("m365", "exo", "spo"):
            return Resp(400, {"error": "service must be 'm365' (Graph), 'exo' (Exchange), or "
                                       "'spo' (SharePoint)"})
        from ..core.memory import VaultStore
        if tenant not in VaultStore().list_clients():
            return Resp(400, {"error": "pick a registered client to sign in"})
        try:
            r = m365_auth.start_device_auth(get_config(), tenant, service=service)
        except credentials.MissingCredential as e:
            return Resp(400, {"error": str(e)})
        except Exception as e:                       # noqa: BLE001
            return Resp(502, {"error": f"could not start Microsoft sign-in: {e}"})
        return Resp(200, r)

    def _m365_oauth_poll(self, body: dict, user: str) -> Resp:
        flow_id = str((body or {}).get("flow_id") or "").strip()
        if not flow_id:
            return Resp(400, {"error": "flow_id required"})
        from ..core import m365_auth
        from ..core.config import get_config
        cfg = get_config()
        service = (m365_auth._flows.get(flow_id) or {}).get("service", "m365")
        try:
            status, msg = m365_auth.poll_device_auth(flow_id, cfg)
        except Exception as e:                       # noqa: BLE001
            return Resp(502, {"error": f"Microsoft sign-in poll failed: {e}"})
        if status == "pending":
            return Resp(200, {"status": "pending"})
        if status == "error":
            return Resp(200, {"status": "error", "error": msg})
        try:
            get_client_factory().invalidate(service)
        except Exception:
            pass
        label = {"exo": "Exchange Online", "spo": "SharePoint Online"}.get(service, "M365")
        self.agent.audit.record(actor=user, tenant_id=msg or "*", action="credential_set",
                                tool=service,
                                detail=f"{label} connected for client '{msg}' (device-code)")
        return Resp(200, {"status": "connected", "tenant": msg, "service": service})

    def _m365_renew(self, body: dict, user: str) -> Resp:
        """Force a token keep-alive now — one client (body.tenant) or all connected (D-35)."""
        from ..core import m365_auth
        from ..core.config import get_config
        cfg = get_config()
        from ..core.credvault import VaultLocked
        tenant = str((body or {}).get("tenant") or "").strip()
        service = str((body or {}).get("service") or "m365").strip()
        if tenant:
            try:
                ok = m365_auth.renew(cfg, tenant, service)
                result = {"ok": [tenant], "failed": []} if ok else {"ok": [], "failed": [tenant]}
            except VaultLocked:
                result = {"ok": [], "failed": [], "locked": [tenant]}
        else:
            result = m365_auth.renew_all(cfg)        # every client, every service (Graph + EXO)
        result.setdefault("locked", [])
        try:
            get_client_factory().invalidate("m365")
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id=tenant or "*", action="config_change",
                                tool="m365", detail=f"M365 token renew ({len(result['ok'])} ok, "
                                                    f"{len(result['failed'])} failed, "
                                                    f"{len(result['locked'])} vault-locked)")
        return Resp(200, {**result, "clients": self._m365_clients().payload["clients"]})

    def _m365_disconnect(self, tenant: str, user: str, service: str = "m365") -> Resp:
        from ..core import m365_auth
        from ..core.config import get_config
        from ..core.credvault import VaultLocked
        label = {"exo": "Exchange Online", "spo": "SharePoint Online"}.get(service, "M365")
        try:
            removed = m365_auth.clear_tokens(get_config(), tenant, service)
        except VaultLocked:
            return Resp(409, {"error": f"the credential vault is locked — unlock it to disconnect "
                                       f"{label} (the stored token must really be deleted)"})
        if not removed:
            return Resp(404, {"error": f"client '{tenant}' was not connected"})
        try:
            get_client_factory().invalidate(service)
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id=tenant, action="config_change",
                                tool=service, detail=f"{label} disconnected for client '{tenant}'")
        return Resp(200, {"ok": True, "tenant": tenant, "service": service})

    # ── Google Workspace per-client OAuth (D-118) — authorization-code flow ──
    def _gws_clients(self) -> Resp:
        """Per-client Google Workspace connection status for the card."""
        from ..core import gws_auth
        from ..core.config import get_config
        from ..core.memory import VaultStore
        cfg = get_config()
        connected = set(gws_auth.list_connected(cfg))
        clients = []
        for c in VaultStore().list_clients():
            row = {"tenant": c, "connected": c in connected,
                   "fingerprint": gws_auth.fingerprint_for(cfg, c) if c in connected else None}
            if c in connected:
                row.update(gws_auth.health(cfg, c))
            clients.append(row)
        return Resp(200, {"app_configured": gws_auth.is_configured(cfg),
                          "redirect_uri": gws_auth._redirect_uri(cfg),
                          "renew_hours": cfg.int("MSPAI_GWS_RENEW_HOURS", 12), "clients": clients})

    def _gws_oauth_start(self, body: dict) -> Resp:
        """Begin a per-client Google sign-in: returns the consent URL the client's super-admin opens.
        Google redirects back to GWS_REDIRECT_URI, handled by gws_oauth_callback (HTML)."""
        from ..core import gws_auth
        from ..core.config import get_config
        from ..core.memory import VaultStore
        tenant = str((body or {}).get("tenant") or "").strip()
        if tenant not in VaultStore().list_clients():
            return Resp(400, {"error": "pick a registered client to sign in"})
        login_hint = str((body or {}).get("login_hint") or "").strip()
        hosted_domain = str((body or {}).get("hosted_domain") or "").strip()
        try:
            r = gws_auth.start_auth(get_config(), tenant, login_hint=login_hint,
                                    hosted_domain=hosted_domain)
        except credentials.MissingCredential as e:
            return Resp(400, {"error": str(e)})
        except Exception as e:                       # noqa: BLE001
            return Resp(502, {"error": f"could not start Google sign-in: {e}"})
        return Resp(200, r)

    def gws_oauth_callback(self, query: dict, user: str) -> str:
        """Handle Google's redirect (?code&state) — a browser GET that returns an HTML page. The
        unguessable, short-lived `state` (issued only to an admin who started the flow via the
        admin-gated /start) authorizes the exchange; no separate role check is possible on a
        third-party redirect. Returns a small self-closing page reporting success/failure."""
        from ..core import gws_auth
        from ..core.config import get_config
        state = str((query or {}).get("state") or "").strip()
        code = str((query or {}).get("code") or "").strip()
        err = str((query or {}).get("error") or "").strip()
        if err:
            return self._gws_callback_html(False, f"Google returned an error: {err}")
        try:
            status, msg = gws_auth.complete_auth(get_config(), state, code)
        except Exception as e:                       # noqa: BLE001
            return self._gws_callback_html(False, f"sign-in failed: {e}")
        if status != "connected":
            return self._gws_callback_html(False, msg or "sign-in failed")
        try:
            get_client_factory().invalidate("gws")
        except Exception:
            pass
        self.agent.audit.record(actor=user or "oauth-callback", tenant_id=msg or "*",
                                action="credential_set", tool="gws",
                                detail=f"Google Workspace connected for client '{msg}' (OAuth)")
        return self._gws_callback_html(True, f"Google Workspace connected for client '{msg}'.")

    @staticmethod
    def _gws_callback_html(ok: bool, message: str) -> str:
        import html as _html
        color = "#16a34a" if ok else "#dc2626"
        icon = "✓" if ok else "✕"
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>Google Workspace</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<style>body{font-family:system-ui,sans-serif;background:#0b0f19;color:#e5e7eb;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
            ".card{background:#111827;border:1px solid #1f2937;border-radius:16px;padding:32px 40px;"
            "text-align:center;max-width:420px}.ic{font-size:44px;line-height:1}"
            "h1{font-size:18px;margin:12px 0 6px}p{color:#9ca3af;font-size:14px;margin:0 0 18px}"
            "button{background:#4f46e5;color:#fff;border:0;border-radius:10px;padding:9px 18px;"
            "font-size:14px;cursor:pointer}</style></head><body><div class='card'>"
            f"<div class='ic' style='color:{color}'>{icon}</div>"
            f"<h1>{'Connected' if ok else 'Not connected'}</h1>"
            f"<p>{_html.escape(message)}</p>"
            "<button onclick='window.close()'>Close this window</button>"
            "<script>try{if(window.opener)window.opener.postMessage("
            f"{{source:'gws-oauth',ok:{'true' if ok else 'false'}}},'*');}}catch(e){{}}</script>"
            "</div></body></html>")

    def _gws_renew(self, body: dict, user: str) -> Resp:
        """Force a token keep-alive now — one client (body.tenant) or all connected."""
        from ..core import gws_auth
        from ..core.config import get_config
        from ..core.credvault import VaultLocked
        cfg = get_config()
        tenant = str((body or {}).get("tenant") or "").strip()
        if tenant:
            try:
                ok = gws_auth.renew(cfg, tenant)
                result = {"ok": [tenant], "failed": []} if ok else {"ok": [], "failed": [tenant]}
            except VaultLocked:
                result = {"ok": [], "failed": [], "locked": [tenant]}
        else:
            result = gws_auth.renew_all(cfg)
        result.setdefault("locked", [])
        try:
            get_client_factory().invalidate("gws")
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id=tenant or "*", action="config_change",
                                tool="gws", detail=f"Google Workspace token renew "
                                f"({len(result['ok'])} ok, {len(result['failed'])} failed, "
                                f"{len(result['locked'])} vault-locked)")
        return Resp(200, {**result, "clients": self._gws_clients().payload["clients"]})

    def _gws_disconnect(self, tenant: str, user: str) -> Resp:
        from ..core import gws_auth
        from ..core.config import get_config
        from ..core.credvault import VaultLocked
        try:
            removed = gws_auth.clear_tokens(get_config(), tenant)
        except VaultLocked:
            return Resp(409, {"error": "the credential vault is locked — unlock it to disconnect "
                                       "Google Workspace (the stored token must really be deleted)"})
        if not removed:
            return Resp(404, {"error": f"client '{tenant}' was not connected"})
        try:
            get_client_factory().invalidate("gws")
        except Exception:
            pass
        self.agent.audit.record(actor=user, tenant_id=tenant, action="config_change",
                                tool="gws", detail=f"Google Workspace disconnected for client '{tenant}'")
        return Resp(200, {"ok": True, "tenant": tenant})

    def _set_capability(self, name: str, body: dict, user: str = "owner") -> Resp:
        if self.agent.registry.get(name) is None:
            return Resp(404, {"error": f"unknown tool '{name}'"})
        if "enabled" in body:
            self.agent.audit.set_enabled(name, bool(body["enabled"]))
        kw = {k: bool(body[k]) for k in ("allow_write", "require_approval") if k in body}
        pol = self.agent.caps.set(name, **kw) if kw else \
            self.agent.caps.get(name, default_enabled=True)
        # Audit the throttle change (Rule §2.4) — enabling a WRITE tool is a high-trust action and
        # must be attributable. Records exactly what changed.
        changed = {k: bool(body[k]) for k in ("enabled", "allow_write", "require_approval")
                   if k in body}
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change", tool=name,
                                detail=f"capability {name}: {changed}")
        return Resp(200, {"name": name,
                          "enabled": self.agent.audit.is_enabled(name, True),
                          "allow_write": pol.allow_write,
                          "require_approval": pol.require_approval})

    def _user_profile(self, user: str) -> dict:
        """Who the agent is talking to (D-31) — account facts injected into the system prompt."""
        u = self.auth.get_user(user) or {}
        return {"username": user, "email": u.get("email") or "", "role": u.get("role") or ""}

    def _me_memory_get(self, user: str) -> Resp:
        from ..core.memory import VaultStore
        return Resp(200, {"username": user, "memory": VaultStore().read_user_memory(user)})

    def _me_memory_set(self, body: dict, user: str) -> Resp:
        from ..core.memory import VaultStore
        r = VaultStore().write_user_memory(user, body.get("content") or "", user)
        if r.get("error"):
            return Resp(400, r)
        self.agent.audit.record(actor=user, tenant_id="*", action="config_change",
                                detail="user_memory_update (self)")
        return Resp(200, r)

    @staticmethod
    def _detect_single_client(message: str, clients: list) -> Optional[str]:
        """If the message names EXACTLY ONE registered client, return it (D-52). Reliable, model-
        independent locking: names are matched as whole tokens with _/-/space interchangeable, so
        'acme test', 'Acme_Test', 'acme-test' all hit the client 'Acme_Test'. Two+ distinct
        clients named → None (ambiguous; let it stay all-clients / aggregate)."""
        import re as _re
        norm = lambda s: _re.sub(r"[\s_-]+", " ", (s or "").lower()).strip()
        msg = " " + norm(message) + " "
        hits = []
        for c in clients:
            n = norm(c)
            if n and _re.search(r"(?<![a-z0-9])" + _re.escape(n) + r"(?![a-z0-9])", msg):
                hits.append(c)
        return hits[0] if len(set(hits)) == 1 else None

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
                          _meta={"tasks": self.agent.tasks, "credvault": self.agent.credvault,
                                 "user_profile": self._user_profile(user),
                                 "conversation_id": conv_id})
        # Server-side history is authoritative (the browser no longer holds the transcript).
        history = convs.history(user, conv_id)
        convs.add_message(user, conv_id, "user", message)
        turn = self.agent.chat(ctx, message, model_id=model_id, history=history,
                               profile=body.get("agent") or body.get("profile"))
        convs.add_message(user, conv_id, "assistant", turn.answer, meta={
            "tools": turn.tool_events, "citations": turn.citations, "pending": turn.pending,
            "reasoning": turn.reasoning or None,
            "label": f"{turn.provider}/{turn.model} · {turn.rounds} round(s)"})
        title = next((c["title"] for c in convs.list(user) if c["id"] == conv_id), "")
        return Resp(200, {
            "answer": turn.answer, "citations": turn.citations,
            "tool_events": turn.tool_events, "provider": turn.provider,
            "model": turn.model, "rounds": turn.rounds, "tenant": tenant,
            "pending": turn.pending, "reasoning": turn.reasoning or None,
            "conversation_id": conv_id, "title": title,
            "suggest_skill": None if turn.pending else self._suggest_skill(message, turn),
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
        # D-52: an all-clients chat LOCKS onto a client the moment the message clearly names one —
        # deterministically (not model-dependent), so per-client tools work this turn and the
        # thread stays scoped. Switching clients is a new chat (handled in the UI).
        auto_focus = None
        if tenant == "*":
            from ..core.memory import VaultStore
            hit = self._detect_single_client(message, VaultStore().list_clients())
            if hit:
                tenant = hit
                convs.set_tenant(user, conv_id, hit)
                auto_focus = hit
        prior = convs.history(user, conv_id)
        convs.add_message(user, conv_id, "user", message)
        yield {"type": "start", "conversation_id": conv_id, "tenant": tenant}
        if auto_focus:
            yield {"type": "client_locked", "tenant": auto_focus}

        q: "queue.Queue" = queue.Queue()
        DONE = object()
        result: dict = {}
        stop_ev = threading.Event()
        with self._stops_lock:                           # let POST /api/chat/stop reach this turn
            self._stops[conv_id] = stop_ev

        def run():
            try:
                ctx = ToolContext(
                    tenant_id=tenant, actor=user,
                    allow_cloud=bool(model_id and not model_id.startswith("ollama:")),
                    client_factory=get_client_factory(),
                    _meta={"tasks": self.agent.tasks, "credvault": self.agent.credvault,
                           "user_profile": self._user_profile(user), "conversation_id": conv_id})
                result["turn"] = self.agent.chat_stream(
                    ctx, message, lambda e: q.put(e), model_id=model_id, history=prior,
                    profile=body.get("agent") or body.get("profile"),
                    should_stop=stop_ev.is_set)
            except Exception as e:                       # contained; surfaced as an SSE error frame
                result["error"] = f"{type(e).__name__}: {e}"
                import sys, traceback                     # log the FULL trace to journald (D-95)
                print(f"[chat_stream] turn failed for {user}/{conv_id}: {result['error']}\n"
                      + traceback.format_exc(), file=sys.stderr, flush=True)
            finally:
                q.put(DONE)

        threading.Thread(target=run, daemon=True).start()
        try:
            while True:
                ev = q.get()
                if ev is DONE:
                    break
                yield ev
        finally:
            with self._stops_lock:
                self._stops.pop(conv_id, None)

        if "error" in result:
            yield {"type": "error", "error": result["error"]}
            return
        turn = result["turn"]
        # The turn locked this all-clients chat onto one client (D-52) — persist the binding so
        # every future message in this thread is scoped to it. `auto_focus` (server-side name
        # detection above) already re-bound; this covers the model-driven focus_client path.
        if turn.focus_client and not auto_focus:
            convs.set_tenant(user, conv_id, turn.focus_client)
            tenant = turn.focus_client
        locked = auto_focus or turn.focus_client
        convs.add_message(user, conv_id, "assistant", turn.answer, meta={
            "tools": turn.tool_events, "citations": turn.citations, "stopped": turn.stopped,
            "pending": turn.pending, "reasoning": turn.reasoning or None,
            "label": f"{turn.provider}/{turn.model} · {turn.rounds} round(s)"})
        title = next((c["title"] for c in convs.list(user) if c["id"] == conv_id), "")
        yield {"type": "answer", "answer": turn.answer, "citations": turn.citations,
               "tool_events": turn.tool_events, "provider": turn.provider, "model": turn.model,
               "rounds": turn.rounds, "tenant": tenant, "conversation_id": conv_id, "title": title,
               "reasoning": turn.reasoning or None,
               "stopped": turn.stopped, "pending": turn.pending, "focus_client": locked,
               "suggest_skill": None if (turn.stopped or turn.pending) else self._suggest_skill(message, turn)}

    def stop_chat(self, body: dict, user: str) -> Resp:
        """Interrupt the caller's in-flight chat turn (D-45). Owner-scoped: a user can only stop a
        conversation they own. The agent loop unwinds at its next safe point (between tokens / before
        the next tool), so no queued write fires after the stop."""
        conv_id = str((body or {}).get("conversation_id") or "").strip()
        if not conv_id or not self.agent.conversations.owns(user, conv_id):
            return Resp(404, {"error": "no such conversation"})
        with self._stops_lock:
            ev = self._stops.get(conv_id)
        if ev is not None:
            ev.set()
        return Resp(200, {"ok": True, "stopping": ev is not None})

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
