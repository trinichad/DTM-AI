"""Teams bot bridge (D-29) — Bot Framework webhook → the SAME guarded agent loop.

Order of checks for every incoming activity (each one fail-closed):
  1. kill switch: MSPAI_TEAMS=0 disables the webhook entirely (I-4)
  2. integration configured (credentials.require fails closed)
  3. Bot Framework JWT verified (issuer/audience/signature/expiry)
  4. activity dedup (Bot Framework redelivers on slow responses)
  5. user ALLOWLIST — default deny on the AAD object id (Hermes model)
  6. group/channel messages only when the bot is @mentioned (DMs always)

Replies are produced by Agent.chat() — profile TEAMS_PROFILE, tenant fixed server-side to
TEAMS_BIND_TENANT, local-first unless TEAMS_ALLOW_CLOUD=1 (Rule #5). The webhook returns
202 immediately; the turn runs in a background thread and the answer is POSTed back as a
proactive activity. Every tool call inside the turn is dispatch()-guarded + audited as usual.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import Any, Optional

from .config import get_config
from ..clients.msteams import TeamsAuthError, user_allowed, verify_bot_jwt

_AT_TAG_RE = re.compile(r"<at>[^<]*</at>\s*")
_MAX_TEXT = 8000          # inbound message cap (Teams text can be huge; the LLM doesn't need it)
_MAX_REPLY = 24000        # Teams message limit is ~28 KB — stay under it


class _Dedup:
    """Tiny LRU set of recently seen activity ids."""

    def __init__(self, size: int = 512) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def seen(self, key: str) -> bool:
        if not key:
            return False
        with self._lock:
            if key in self._seen:
                return True
            self._seen[key] = None
            if len(self._seen) > self._size:
                self._seen.popitem(last=False)
            return False


class TeamsBridge:
    def __init__(self, agent: Any, *, verify_jwt=verify_bot_jwt, user_lookup=None) -> None:
        self.agent = agent
        self._dedup = _Dedup()
        self._verify_jwt = verify_jwt              # injectable for tests
        self._user_lookup = user_lookup            # username -> account dict (AuthStore.get_user)
        self._denied_notified = _Dedup(128)        # tell each denied conversation once
        # per-conversation profile choice (D-88) — in-memory; falls back to TEAMS_PROFILE on restart
        self._conv_profile: dict[str, str] = {}
        self._profile_lock = threading.Lock()

    def _user_profile(self, env: dict, aad_id: str, display_name: str) -> dict:
        """Resolve who this Teams user is (D-31). If their allowlist entry links a dashboard
        account, use THAT identity (email + saved profile memory); else a teams-scoped one."""
        from ..clients.msteams import parse_allowlist
        entry = next((e for e in parse_allowlist(env.get("TEAMS_ALLOWED_USERS") or "")
                      if e["id"] == aad_id), None)
        linked = (entry or {}).get("user") or ""
        if linked and self._user_lookup is not None:
            acct = self._user_lookup(linked)
            if acct:
                return {"username": linked, "email": acct.get("email") or "",
                        "role": acct.get("role") or "", "name": display_name or linked}
        return {"username": f"teams:{aad_id}" if aad_id else "", "email": "",
                "role": "", "name": display_name}

    # ── config helpers ──
    def _env(self) -> Optional[dict[str, str]]:
        from . import credentials
        try:
            return credentials.require("msteams")
        except credentials.MissingCredential:
            return None

    def enabled(self) -> bool:
        return get_config().bool("MSPAI_TEAMS", True)

    def _client(self):
        from ..runtime import get_client_factory
        return get_client_factory()("msteams", "*")

    # ── the webhook entry point ──
    def handle(self, auth_header: str, activity: dict) -> tuple[int, dict]:
        """Process one Bot Framework activity. Returns (http_status, json_body)."""
        if not self.enabled():
            return 404, {"error": "teams webhook disabled"}
        env = self._env()
        if env is None:
            return 404, {"error": "teams integration not configured"}

        try:
            self._verify_jwt(auth_header, env.get("TEAMS_CLIENT_ID", ""))
        except TeamsAuthError as e:
            self.agent.audit.record(actor="teams:unverified", tenant_id="*",
                                    action="login", result_ok=False,
                                    detail=f"teams webhook JWT rejected: {e}")
            return 401, {"error": "unauthorized"}

        if not isinstance(activity, dict):
            return 400, {"error": "bad activity"}
        if self._dedup.seen(str(activity.get("id") or "")):
            return 200, {"ok": True, "deduped": True}
        if activity.get("type") != "message":
            return 200, {"ok": True, "ignored": activity.get("type") or "?"}

        frm = activity.get("from") or {}
        aad_id = str(frm.get("aadObjectId") or frm.get("id") or "")
        user_name = str(frm.get("name") or "unknown")
        conv = activity.get("conversation") or {}
        conv_id = str(conv.get("id") or "")
        service_url = str(activity.get("serviceUrl") or "")
        if not conv_id:
            return 400, {"error": "no conversation id"}

        allowed, reason = user_allowed(env, aad_id)
        if not allowed:
            self.agent.audit.record(actor=f"teams:{user_name} ({aad_id or '?'})", tenant_id="*",
                                    action="login", result_ok=False,
                                    detail=f"teams user denied: {reason}")
            # tell the human once per conversation why nothing is happening (Rule #7: decline + log)
            if not self._denied_notified.seen(conv_id):
                self._send_safe(env, conv_id, service_url,
                                f"⛔ Not authorized. {reason}", reply_to="")
            return 200, {"ok": True, "denied": True}

        text = str(activity.get("text") or "")
        mentioned = self._bot_mentioned(activity, env.get("TEAMS_CLIENT_ID", ""))
        text = _AT_TAG_RE.sub("", text).strip()[:_MAX_TEXT]
        conv_type = str(conv.get("conversationType") or "personal")
        # A card button (Approve/Deny/…) arrives as a message with `value` and (usually) no text —
        # it's always intentional, so skip the @mention / empty-text gating for it.
        value = activity.get("value") if isinstance(activity.get("value"), dict) else None
        is_action = bool(value and value.get("mspai_action"))
        if not is_action:
            if conv_type != "personal" and not mentioned:
                return 200, {"ok": True, "ignored": "group message without @mention"}
            if not text:
                return 200, {"ok": True, "ignored": "empty message"}

        # 202 now; the agent turn / command / approval runs in the background and replies proactively.
        threading.Thread(
            target=self._dispatch,
            args=(env, activity, aad_id, user_name, conv_id, service_url, text, value),
            daemon=True,
        ).start()
        return 202, {"ok": True}

    # ── route a background unit of work: card action → slash command → chat turn (D-88) ──
    def _dispatch(self, env: dict, activity: dict, aad_id: str, user_name: str, conv_id: str,
                  service_url: str, text: str, value: Optional[dict]) -> None:
        actor = f"teams:{user_name} ({aad_id})"
        reply_to = str(activity.get("id") or "")
        try:
            if value and value.get("mspai_action"):          # Approve/Deny card button
                self._do_decision(env, aad_id, user_name, conv_id, service_url, reply_to,
                                  str(value.get("mspai_action")), value.get("approval_id"),
                                  bool(value.get("batch")))
                return
            if text.startswith("/"):
                dec = self._parse_decision(text)            # /approve /repeat /deny
                if dec:
                    self._do_decision(env, aad_id, user_name, conv_id, service_url, reply_to, *dec)
                    return
                reply = self._command(env, aad_id, user_name, conv_id, service_url, text)
                self._send_safe(env, conv_id, service_url, reply, reply_to=reply_to)
                return
            self._run_turn(env, activity, aad_id, user_name, conv_id, service_url, text)
        except Exception as e:                     # contained — always answer SOMETHING
            self.agent.audit.record(actor=actor, tenant_id="*", action="tool_call",
                                    tool="teams_bridge", result_ok=False,
                                    detail=f"teams dispatch failed: {type(e).__name__}: {e}")
            self._send_safe(env, conv_id, service_url,
                            "⚠️ Something went wrong handling that — the error is in the MSP AI "
                            "audit log.", reply_to=reply_to)

    # ── profiles / agents (D-88) ──
    def _active_profile(self, conv_id: str, env: dict) -> str:
        with self._profile_lock:
            chosen = self._conv_profile.get(conv_id)
        return chosen or ((env.get("TEAMS_PROFILE") or "").strip() or "default")

    # ── slash commands (D-88) ──
    _HELP = (
        "**Commands**\n"
        "• `/agents` — list the agents you can switch to\n"
        "• `/agent <name>` — switch this chat to another agent\n"
        "• `/whoami` — who you are, the active agent, and the client scope\n"
        "• `/approve [#id]` — approve a pending action (latest if no id)\n"
        "• `/repeat [#id]` — approve **and** auto-approve the next repeats (15 min)\n"
        "• `/deny [#id]` — reject a pending action\n"
        "• `/help` — this list\n\n"
        "_When an action needs sign-off I post Approve / Approve+repeat / Deny buttons — tap those "
        "or use the commands above._"
    )

    def _command(self, env: dict, aad_id: str, user_name: str, conv_id: str,
                 service_url: str, text: str) -> str:
        parts = text[1:].split()
        cmd = parts[0].lower() if parts else ""
        rest = parts[1:]

        if cmd in ("help", "commands", "?", ""):
            return self._HELP
        if cmd in ("agents", "profiles", "agent-list"):
            from .agents import list_agents
            active = self._active_profile(conv_id, env)
            lines = [("• **" + a["id"] + "**" + (" ⭐" if a["id"] == active else "")
                      + (f" — {a['role']}" if a.get("role") else "")) for a in list_agents()]
            return ("**Agents** (⭐ = active here):\n" + "\n".join(lines)
                    + "\n\nSwitch with `/agent <name>`.")
        if cmd in ("agent", "profile", "use", "switch"):
            if not rest:
                return "Usage: `/agent <name>` — see `/agents` for the list."
            from .agents import list_agents
            name = " ".join(rest).strip()
            agents = list_agents()
            match = next((a for a in agents if a["id"].lower() == name.lower()
                          or (a.get("name") or "").lower() == name.lower()), None)
            if not match:
                return (f"No agent called **{name}**. Available: "
                        + ", ".join(a["id"] for a in agents) + ".")
            with self._profile_lock:
                self._conv_profile[conv_id] = match["id"]
            return (f"✅ Switched to **{match['name']}**"
                    + (f" — {match['role']}" if match.get("role") else "") + " for this chat.")
        if cmd in ("whoami", "me", "who"):
            prof = self._user_profile(env, aad_id, user_name)
            tenant = (env.get("TEAMS_BIND_TENANT") or "*").strip() or "*"
            linked = (f"linked to **{prof['username']}** (role {prof.get('role') or '—'})"
                      if prof.get("role") else "not linked to a dashboard account")
            return (f"You're **{user_name}** — {linked}.\n"
                    f"Active agent: **{self._active_profile(conv_id, env)}**\n"
                    f"Client scope: **{tenant}**")
        # approve/repeat/deny are handled before _command (so they run + continue); fall through here
        return f"Unknown command `/{cmd}`.\n\n" + self._HELP

    # ── approve / repeat / deny a pending action (card button OR slash command) (D-88) ──
    @staticmethod
    def _parse_decision(text: str):
        """A leading-slash approval command → (action, approval_id|None, batch). None if not one."""
        parts = text[1:].split()
        cmd = parts[0].lower() if parts else ""
        ids = [int(a.lstrip("#")) for a in parts[1:] if a.lstrip("#").isdigit()]
        pid = ids[0] if ids else None
        if cmd in ("approve", "yes", "ok"):
            return "approve", pid, False
        if cmd in ("repeat", "approveall", "approve-repeat"):
            return "approve", pid, True
        if cmd in ("deny", "reject", "no"):
            return "deny", pid, False
        return None

    def _decide(self, env: dict, aad_id: str, user_name: str, conv_id: str,
                action: str, approval_id: Any, batch: bool) -> dict:
        """Returns {'reply': str, 'continue': (row, result)|None}. 'continue' is set only when an
        approve EXECUTED successfully — the caller then runs the natural follow-up turn."""
        prof = self._user_profile(env, aad_id, user_name)
        if prof.get("role") != "admin":
            return {"reply": "⛔ Only an **admin-linked** account can approve or deny from Teams. "
                             "Link your AAD id to your admin dashboard account on the Microsoft "
                             "Teams integration card.", "continue": None}
        user = prof["username"]
        actor_match = f"teams:{user_name} ({aad_id})"
        try:
            approval_id = int(approval_id) if approval_id is not None else self._latest_pending(actor_match)
        except (TypeError, ValueError):
            approval_id = None
        if approval_id is None:
            return {"reply": "No pending action found to act on. (Use `/approve #<id>`.)",
                    "continue": None}
        row = self.agent.approvals.get(approval_id)
        if not row:
            return {"reply": f"Approval **#{approval_id}** not found.", "continue": None}
        if row.get("status") != "pending":
            return {"reply": f"Approval **#{approval_id}** is already **{row.get('status')}**.",
                    "continue": None}

        if action == "deny":
            self.agent.approvals.reject(approval_id, by=user)
            self.agent.audit.record(actor=user, tenant_id=row.get("tenant_id") or "*",
                                    action="approval_rejected", tool=row.get("tool"),
                                    category=row.get("category"), result_ok=True,
                                    detail=f"approval#{approval_id} (via teams)")
            return {"reply": f"⛔ Denied **{row.get('tool')}** (#{approval_id}). Nothing ran.",
                    "continue": None}

        # approve → execute exactly as proposed (args-bound), once
        from .gates import AlwaysApprove
        from .context import ToolContext
        from .dispatch import dispatch
        from ..runtime import get_client_factory
        if not self.agent.approvals.claim_for_execution(approval_id, by=user):
            return {"reply": f"Approval **#{approval_id}** was already decided.", "continue": None}
        ctx = ToolContext(tenant_id=row["tenant_id"], actor=f"{user} (teams approval#{approval_id})",
                          client_factory=get_client_factory())
        res = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx,
                       name=row["tool"], args=row["args"], gate=AlwaysApprove())
        self.agent.approvals.mark_result(approval_id, bool(res["ok"]))
        self.agent.audit.record(actor=user, tenant_id=row["tenant_id"], action="approval_executed",
                                tool=row["tool"], category=row["category"], result_ok=bool(res["ok"]),
                                detail=f"approval#{approval_id} (via teams)")
        if res["ok"]:
            summary = f"✅ Approved & ran **{row['tool']}** (#{approval_id})."
        else:
            summary = f"⚠️ Ran **{row['tool']}** (#{approval_id}) but it failed: {res.get('error')}"
        if batch:
            if not res["ok"]:
                summary += "\n🔁 _No auto-approval armed — the first run failed._"
            elif hasattr(self.agent.gate, "grant_batch"):
                g = self.agent.gate.grant_batch(row["tenant_id"], row["tool"],
                                                approval_id=approval_id, by=user)
                if g:
                    self.agent.audit.record(actor=user, tenant_id=row["tenant_id"],
                                            action="approval_batch_granted", tool=row["tool"],
                                            category=row["category"], result_ok=True,
                                            detail=f"approval#{approval_id} × {g['granted']} (teams)")
                    summary += (f"\n🔁 _Auto-approving the next {g['granted']} `{row['tool']}` runs "
                                f"for **{row['tenant_id']}** (15 min)._")
                else:
                    summary += "\n⚠️ _Destructive actions can never be batch-approved._"
        return {"reply": summary, "continue": (row, res) if res["ok"] else None}

    def _do_decision(self, env: dict, aad_id: str, user_name: str, conv_id: str, service_url: str,
                     reply_to: str, action: str, approval_id: Any, batch: bool) -> None:
        """Post the decision result, then (on a successful approve) run the natural follow-up turn
        and post it — the agent continues the task just like the web chat does (D-62)."""
        d = self._decide(env, aad_id, user_name, conv_id, action, approval_id, batch)
        self._send_safe(env, conv_id, service_url, d["reply"], reply_to=reply_to)
        if d.get("continue"):
            row, res = d["continue"]
            try:
                self._continue_after_approval(env, aad_id, user_name, conv_id, service_url,
                                              reply_to, row, res)
            except Exception as e:                 # the action already ran + was reported; don't mask it
                self.agent.audit.record(actor=f"teams:{user_name} ({aad_id})", tenant_id="*",
                                        action="approval_continuation_failed", tool=row.get("tool"),
                                        result_ok=False, detail=str(e)[:200])

    def _continue_after_approval(self, env: dict, aad_id: str, user_name: str, conv_id: str,
                                 service_url: str, reply_to: str, row: dict, res: dict) -> None:
        """Re-invoke the agent (as this chat's profile) so it verifies the outcome, does any remaining
        steps, and replies naturally — posting the follow-up (and a new approval card if it pends)."""
        import json as _json
        from .context import ToolContext
        from ..runtime import get_client_factory
        convs = self.agent.conversations
        owner = f"teams:{aad_id}"
        mspai = next((c for c in convs.list(owner) if c.get("title") == conv_id), None)
        if not mspai:
            return
        mspai_id = mspai["id"]
        tenant = (env.get("TEAMS_BIND_TENANT") or "*").strip() or "*"
        allow_cloud = str(env.get("TEAMS_ALLOW_CLOUD") or "").strip() in ("1", "true", "yes")
        profile = self._active_profile(conv_id, env)
        ctx = ToolContext(tenant_id=tenant, actor=f"teams:{user_name} ({aad_id}) (continuation)",
                          allow_cloud=allow_cloud, client_factory=get_client_factory(),
                          _meta={"tasks": getattr(self.agent, "tasks", None),
                                 "credvault": getattr(self.agent, "credvault", None),
                                 "user_profile": self._user_profile(env, aad_id, user_name)})
        result_blob = _json.dumps({k: res.get(k) for k in ("ok", "data", "error")}, default=str)[:4000]
        synthetic = (f"[system note — not from the owner] The owner APPROVED the pending "
                     f"'{row['tool']}' action and it has ALREADY RUN. Result: {result_blob}. Continue "
                     f"the task: verify the outcome if appropriate, perform any remaining steps, and "
                     f"give the owner a short natural status reply. Do NOT run '{row['tool']}' again "
                     f"with the same arguments.")
        history = convs.history(owner, mspai_id)
        turn = self.agent.chat(ctx, synthetic, history=history, profile=profile)
        answer = (turn.answer or "").strip()
        if not (answer or turn.pending):
            return
        convs.add_message(owner, mspai_id, "assistant", turn.answer, meta={
            "tools": turn.tool_events, "citations": turn.citations, "pending": turn.pending,
            "label": f"{turn.provider}/{turn.model} via teams (continuation)"})
        if turn.citations:
            answer += "\n\n_" + " · ".join(sorted(set(turn.citations))) + "_"
        if answer:
            self._send_safe(env, conv_id, service_url, answer[:_MAX_REPLY], reply_to=reply_to)
        if turn.pending and turn.pending.get("id") is not None:    # chained approval → another card
            try:
                self._client().send_card(conv_id, self._approval_card(turn.pending),
                                         text="_Approve below, or use `/approve`, `/repeat`, `/deny`._",
                                         service_url=service_url, reply_to=reply_to)
            except Exception:
                pass

    def _latest_pending(self, actor: str) -> Optional[int]:
        try:
            rows = self.agent.approvals.list("pending")
        except Exception:
            return None
        mine = [r for r in rows if str(r.get("actor") or "") == actor and r.get("id") is not None]
        return max((int(r["id"]) for r in mine), default=None)

    @staticmethod
    def _approval_card(pending: dict) -> dict:
        import json as _json
        pid = pending.get("id")
        tool = str(pending.get("tool") or "?")
        tenant = str(pending.get("tenant") or "*")
        args = _json.dumps(pending.get("args") or {}, default=str)[:600]
        return {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard", "version": "1.4",
            "body": [
                {"type": "TextBlock", "text": "⚠️ Approval needed", "weight": "Bolder",
                 "color": "Warning", "size": "Medium"},
                {"type": "FactSet", "facts": [{"title": "Action", "value": tool},
                                              {"title": "Client", "value": tenant},
                                              {"title": "ID", "value": f"#{pid}"}]},
                {"type": "TextBlock", "text": args, "wrap": True, "isSubtle": True,
                 "fontType": "Monospace", "spacing": "Small"},
            ],
            "actions": [
                {"type": "Action.Submit", "title": "✅ Approve",
                 "data": {"mspai_action": "approve", "approval_id": pid}},
                {"type": "Action.Submit", "title": "🔁 Approve + repeat",
                 "data": {"mspai_action": "approve", "approval_id": pid, "batch": True}},
                {"type": "Action.Submit", "title": "⛔ Deny",
                 "data": {"mspai_action": "deny", "approval_id": pid}},
            ],
        }

    @staticmethod
    def _bot_mentioned(activity: dict, client_id: str) -> bool:
        for ent in activity.get("entities") or []:
            if (ent or {}).get("type") == "mention":
                mid = str(((ent.get("mentioned") or {}).get("id")) or "")
                # the bot's channel account id is "28:<client-id>"
                if client_id and (mid == f"28:{client_id}" or mid.endswith(client_id)):
                    return True
        # fall back: any <at> tag in a non-personal chat means someone was mentioned;
        # Teams only delivers channel messages to the bot when IT was mentioned.
        return "<at>" in str(activity.get("text") or "")

    # ── the actual turn ──
    def _run_turn(self, env: dict, activity: dict, aad_id: str, user_name: str,
                  conv_id: str, service_url: str, text: str) -> None:
        actor = f"teams:{user_name} ({aad_id})"
        reply_to = str(activity.get("id") or "")
        try:
            client = self._client()
            client.send_typing(conv_id, service_url=service_url)

            from ..runtime import get_client_factory
            from .context import ToolContext
            tenant = (env.get("TEAMS_BIND_TENANT") or "*").strip() or "*"
            allow_cloud = str(env.get("TEAMS_ALLOW_CLOUD") or "").strip() in ("1", "true", "yes")
            profile = self._active_profile(conv_id, env)      # per-chat choice, else TEAMS_PROFILE (D-88)
            ctx = ToolContext(tenant_id=tenant, actor=actor, allow_cloud=allow_cloud,
                              client_factory=get_client_factory(),
                              _meta={"tasks": getattr(self.agent, "tasks", None),
                                     "credvault": getattr(self.agent, "credvault", None),
                                     "user_profile": self._user_profile(env, aad_id, user_name)})

            convs = self.agent.conversations
            owner = f"teams:{aad_id}"
            mspai_conv = next((c for c in convs.list(owner) if c.get("title") == conv_id), None)
            mspai_conv_id = mspai_conv["id"] if mspai_conv else \
                convs.create(owner, tenant_id=tenant, title=conv_id)["id"]
            history = convs.history(owner, mspai_conv_id)
            convs.add_message(owner, mspai_conv_id, "user", text)

            turn = self.agent.chat(ctx, text, history=history, profile=profile)
            answer = (turn.answer or "(no answer)").strip()
            if turn.citations:
                answer += "\n\n_" + " · ".join(sorted(set(turn.citations))) + "_"
            convs.add_message(owner, mspai_conv_id, "assistant", turn.answer, meta={
                "tools": turn.tool_events, "citations": turn.citations, "pending": turn.pending,
                "label": f"{turn.provider}/{turn.model} via teams"})
            client.send_text(conv_id, answer[:_MAX_REPLY],
                             service_url=service_url, reply_to=reply_to)
            # a write is paused on approval → post the Approve / Approve+repeat / Deny buttons (D-88)
            if turn.pending and turn.pending.get("id") is not None:
                try:
                    client.send_card(conv_id, self._approval_card(turn.pending),
                                     text="_Approve below, or use `/approve`, `/repeat`, `/deny`._",
                                     service_url=service_url, reply_to=reply_to)
                except Exception:
                    self._send_safe(env, conv_id, service_url,
                                    f"Action **{turn.pending.get('tool')}** (#{turn.pending.get('id')}) "
                                    f"needs sign-off — reply `/approve` or `/deny`.", reply_to=reply_to)
        except Exception as e:                     # contained — always answer SOMETHING
            self.agent.audit.record(actor=actor, tenant_id="*", action="tool_call",
                                    tool="teams_bridge", result_ok=False,
                                    detail=f"teams turn failed: {type(e).__name__}: {e}")
            self._send_safe(env, conv_id, service_url,
                            "⚠️ Something went wrong handling that — the error is in the "
                            "MSP AI audit log.", reply_to=reply_to)

    def _send_safe(self, env: dict, conv_id: str, service_url: str, text: str,
                   *, reply_to: str) -> None:
        try:
            self._client().send_text(conv_id, text, service_url=service_url, reply_to=reply_to)
        except Exception:
            pass
