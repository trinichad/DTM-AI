"""Every mailbox a USER can access (the reverse lookup) (D-58; robust sweep D-109;
SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_user_mailbox_access"
DESCRIPTION = ("Show every MAILBOX a user has access to — Full Access, Send As, or Send on Behalf. "
               "Sweeps ALL the client's mailboxes (Full Access has no reverse query, so each is "
               "checked) and ALSO runs a direct Send-As reverse lookup so nothing is missed. The "
               "reverse of exo_mailbox_permissions. Pass `limit` only to cap the sweep on a very "
               "large tenant (default = all).")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address"},
        "limit": {"type": "integer",
                  "description": "cap the Full-Access mailbox sweep (default 0 = all mailboxes; "
                                 "set a number only to bound a very large tenant)"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def _has_fullaccess(rows: list[dict]) -> bool:
    return any("fullaccess" in " ".join(str(x) for x in (row.get("AccessRights") or [])).lower()
               for row in rows)


def run(ctx, user: str, limit: int = 0, **_: Any):
    from . import _exo_common as c
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}
    local = user.split("@")[0].lower()
    exo = ctx.client("exo")
    found: dict[str, dict] = {}          # smtp.lower() -> {mailbox, display_name, type, access[]}

    def _add(addr: str, right: str, display=None, mtype=None):
        addr = str(addr or "")
        if not addr or addr.lower() == user.lower():
            return
        e = found.setdefault(addr.lower(), {"mailbox": addr, "display_name": display,
                                            "type": mtype, "access": []})
        if display and not e.get("display_name"):
            e["display_name"] = display
        if mtype and not e.get("type"):
            e["type"] = mtype
        if right not in e["access"]:
            e["access"].append(right)

    def _full(addr: str) -> bool:
        fa = exo.invoke("Get-MailboxPermission", {"Identity": addr, "User": user})
        return not c.err(fa) and _has_fullaccess(_rows(fa))

    # 1) Sweep mailboxes for Full Access + Send-on-Behalf (+ per-box Send-As). No cap by default —
    #    the old 300-cap silently dropped mailboxes past it (e.g. a 't' name), missing access (D-109).
    rs: Any = "Unlimited" if not limit else max(1, min(int(limit), 5000))
    r = exo.invoke("Get-Mailbox", {"ResultSize": rs})
    if c.err(r):
        return {"ok": False, "error": c.err(r)}
    boxes = _rows(r)
    swept: set[str] = set()
    for mb in boxes:
        addr = str(mb.get("PrimarySmtpAddress") or "")
        if not addr or addr.lower() == user.lower():
            continue
        swept.add(addr.lower())
        disp, mtype = mb.get("DisplayName"), mb.get("RecipientTypeDetails")
        if _full(addr):
            _add(addr, "full_access", disp, mtype)
        sa = exo.invoke("Get-RecipientPermission", {"Identity": addr, "Trustee": user})
        if not c.err(sa) and _rows(sa):
            _add(addr, "send_as", disp, mtype)
        sob = [str(x).lower() for x in (mb.get("GrantSendOnBehalfTo") or [])]
        if any(user.lower() == s or local == s for s in sob):
            _add(addr, "send_on_behalf", disp, mtype)

    # 2) Direct Send-As reverse lookup — catches anything the mailbox sweep didn't return (capped,
    #    or a non-mailbox recipient). For each, resolve to the mailbox and also check Full Access,
    #    so a mailbox the sweep missed still reports COMPLETE access (D-109).
    rp = exo.invoke("Get-RecipientPermission", {"Trustee": user})
    for row in _rows(rp) if not c.err(rp) else []:
        ident = str(row.get("Identity") or "")
        rights = " ".join(str(x) for x in (row.get("AccessRights") or [])).lower()
        if not ident or "sendas" not in rights:
            continue
        one = exo.invoke("Get-Mailbox", {"Identity": ident})
        mb1 = _rows(one)
        if mb1:
            addr = str(mb1[0].get("PrimarySmtpAddress") or ident)
            if addr.lower() in swept:
                continue                                 # already handled in the sweep
            _add(addr, "send_as", mb1[0].get("DisplayName"), mb1[0].get("RecipientTypeDetails"))
            if _full(addr):
                _add(addr, "full_access", mb1[0].get("DisplayName"),
                     mb1[0].get("RecipientTypeDetails"))
        elif ident.lower() not in swept:
            _add(ident, "send_as")                       # non-mailbox recipient (group / mail user)

    access = sorted(found.values(), key=lambda x: str(x.get("mailbox") or "").lower())
    out: dict[str, Any] = {"ok": True, "user": user, "count": len(access),
                           "mailboxes": access, "mailboxes_checked": len(swept)}
    if not access:
        out["note"] = (f"{user} has no Full Access / Send-As / Send-on-Behalf on any mailbox "
                       f"(swept {len(swept)})")
    return out
