"""Email client (D-28) — one integration, every transport.

Modes (auto-detected unless EMAIL_MODE pins one):
  api   — smtp2go-style JSON API: POST <EMAIL_API_URL>/email/send, key in X-Smtp2go-Api-Key
          (also placed in the body for relays that expect it there).
  smtp  — stdlib smtplib against any relay (smtp2go SMTP login, M365, postfix, ...), with
          starttls (default) / ssl / none per EMAIL_SMTP_SECURITY.

Recipient floor (exfiltration guard — enforced HERE, not in prose):
  EMAIL_ALLOWED_RECIPIENTS unset  → only EMAIL_DEFAULT_TO may receive mail
  CSV of addresses and/or @domains → only those
  "*"                              → anyone (explicit owner opt-out)
"""
from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Callable, Optional

from ._http import HttpError, http_json

_DEFAULT_API_URL = "https://api.smtp2go.com/v3"
_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailClient:
    def __init__(self, env: dict[str, str], *, transport: Callable = http_json,
                 smtp_factory: Optional[Callable] = None) -> None:
        self.from_addr = (env.get("EMAIL_FROM") or "").strip()
        if not self.from_addr:
            raise ValueError("EMAIL_FROM is required")
        self.api_key = (env.get("EMAIL_API_KEY") or "").strip()
        self.api_url = (env.get("EMAIL_API_URL") or _DEFAULT_API_URL).rstrip("/")
        self.smtp_host = (env.get("EMAIL_SMTP_HOST") or "").strip()
        self.smtp_port = int(env.get("EMAIL_SMTP_PORT") or 587)
        self.smtp_user = (env.get("EMAIL_SMTP_USER") or "").strip()
        self.smtp_pass = env.get("EMAIL_SMTP_PASS") or ""
        self.security = (env.get("EMAIL_SMTP_SECURITY") or "starttls").strip().lower()
        self.default_to = (env.get("EMAIL_DEFAULT_TO") or "").strip()
        self.allowed = (env.get("EMAIL_ALLOWED_RECIPIENTS") or "").strip()
        mode = (env.get("EMAIL_MODE") or "").strip().lower()
        self.mode = mode if mode in ("api", "smtp") else ("api" if self.api_key else "smtp")
        self._t = transport
        self._smtp_factory = smtp_factory          # test seam; default chosen per security mode

    # ── recipient floor ──
    def recipient_allowed(self, to: str) -> tuple[bool, str]:
        addr = parseaddr(to or "")[1].lower()
        if not _ADDR_RE.match(addr):
            return False, f"'{to}' is not a valid email address"
        if self.allowed == "*":
            return True, "ok"
        if not self.allowed:
            default = parseaddr(self.default_to)[1].lower()
            if addr == default and default:
                return True, "ok"
            return False, ("recipient floor: only EMAIL_DEFAULT_TO may receive mail until "
                           "EMAIL_ALLOWED_RECIPIENTS is configured ('*' allows anyone)")
        for entry in self.allowed.split(","):
            entry = entry.strip().lower()
            if not entry:
                continue
            if entry.startswith("@") and addr.endswith(entry):
                return True, "ok"
            if addr == entry:
                return True, "ok"
        return False, f"'{addr}' is not in EMAIL_ALLOWED_RECIPIENTS"

    # ── send ──
    @staticmethod
    def _split_recipients(to: str) -> list[str]:
        """'a@x.com, b@x.com; c@x.com' → list (comma/semicolon separated, deduped, order kept)."""
        out: list[str] = []
        for a in re.split(r"[,;]", to or ""):
            a = a.strip()
            if a and a.lower() not in (x.lower() for x in out):
                out.append(a)
        return out

    def send(self, subject: str, body: str, to: Optional[str] = None,
             html: bool = False, html_body: str = "") -> dict[str, Any]:
        """html=True → `body` IS HTML, sent as-is. html_body (D-38) → multipart/alternative:
        `body` as the plain-text part + `html_body` as the rich part.
        `to` may be MULTIPLE recipients (comma/semicolon separated, D-46) — every address must
        individually pass the recipient floor or NOTHING is sent (fail closed, no partial sends)."""
        addrs = self._split_recipients(to or self.default_to)
        if not addrs:
            return {"ok": False, "error": "no recipient — pass `to` or set EMAIL_DEFAULT_TO"}
        for a in addrs:
            ok, reason = self.recipient_allowed(a)
            if not ok:
                return {"ok": False, "error": reason}
        if not (subject or "").strip():
            return {"ok": False, "error": "subject is required"}
        try:
            if self.mode == "api":
                return self._send_api(addrs, subject, body, html, html_body)
            return self._send_smtp(addrs, subject, body, html, html_body)
        except HttpError as e:
            return {"ok": False, "error": f"email API error: HTTP {e.status}: {e.body[:200]}"}
        except (smtplib.SMTPException, OSError) as e:
            return {"ok": False, "error": f"SMTP error: {e}"}

    def _send_api(self, addrs: list[str], subject: str, body: str, html: bool,
                  html_body: str = "") -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "error": "EMAIL_API_KEY is not set (mode=api)"}
        payload: dict[str, Any] = {
            "api_key": self.api_key, "sender": self.from_addr, "to": addrs, "subject": subject,
            ("html_body" if html else "text_body"): body or "",
        }
        if html_body and not html:
            payload["html_body"] = html_body
        _s, data = self._t("POST", f"{self.api_url}/email/send",
                           headers={"X-Smtp2go-Api-Key": self.api_key}, json_body=payload)
        d = (data or {}).get("data") if isinstance(data, dict) else {}
        failures = (d or {}).get("failures") or []
        if failures:
            return {"ok": False, "error": f"relay rejected: {failures}"}
        return {"ok": True, "to": ", ".join(addrs), "via": "api",
                "email_id": (d or {}).get("email_id") or ""}

    def _smtp_connect(self):
        if self._smtp_factory is not None:
            return self._smtp_factory(self.smtp_host, self.smtp_port)
        if self.security == "ssl":
            return smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
        return smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)

    def _send_smtp(self, addrs: list[str], subject: str, body: str, html: bool,
                   html_body: str = "") -> dict[str, Any]:
        if not self.smtp_host:
            return {"ok": False, "error": "EMAIL_SMTP_HOST is not set (mode=smtp)"}
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.from_addr, ", ".join(addrs), subject
        if html:
            msg.set_content("This message requires an HTML-capable mail client.")
            msg.add_alternative(body or "", subtype="html")
        else:
            msg.set_content(body or "")
            if html_body:
                msg.add_alternative(html_body, subtype="html")
        with self._smtp_connect() as server:
            server.ehlo()
            if self.security == "starttls":
                server.starttls()
                server.ehlo()
            if self.smtp_user:
                server.login(self.smtp_user, self.smtp_pass)
            server.send_message(msg)
        return {"ok": True, "to": ", ".join(addrs), "via": f"smtp:{self.smtp_host}"}

    # ── probe (never sends mail) ──
    def probe(self) -> dict[str, Any]:
        if self.mode == "api":
            if not self.api_key:
                return {"ok": False, "detail": "EMAIL_API_KEY not set"}
            try:
                _s, _d = self._t("POST", f"{self.api_url}/stats/email_summary",
                                 headers={"X-Smtp2go-Api-Key": self.api_key},
                                 json_body={"api_key": self.api_key})
                return {"ok": True, "detail": f"API auth ok at {self.api_url}"}
            except HttpError as e:
                return {"ok": False, "detail": f"API auth failed: HTTP {e.status}"}
        if not self.smtp_host:
            return {"ok": False, "detail": "EMAIL_SMTP_HOST not set"}
        try:
            with self._smtp_connect() as server:
                server.ehlo()
                if self.security == "starttls":
                    server.starttls()
                    server.ehlo()
                if self.smtp_user:
                    server.login(self.smtp_user, self.smtp_pass)
                server.noop()
            return {"ok": True,
                    "detail": f"SMTP {'auth ' if self.smtp_user else ''}ok at "
                              f"{self.smtp_host}:{self.smtp_port} ({self.security})"}
        except (smtplib.SMTPException, OSError) as e:
            return {"ok": False, "detail": f"SMTP: {e}"}
