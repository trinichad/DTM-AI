"""Shared plumbing for the Exchange Online skills (D-55; SOP: exchange-online).

No NAME attribute → the registry skips this module (I-1); it is a library, not a tool.
The pattern every EXO write skill follows (D-43 lesson — never report an unverified write):
  preflight Get-Mailbox (exists, resolves to EXACTLY one) → Set-Mailbox → re-read → compare.
"""
from __future__ import annotations

from typing import Any, Optional


def err(r: Any) -> str:
    return str(r.get("error")) if isinstance(r, dict) and r.get("error") else ""


# Most EXO Set-Mailbox writes are read-your-writes, but a few backend operations are async —
# mailbox-TYPE conversion above all (Set-Mailbox -Type accepts the change but Get-Mailbox keeps
# reporting the old RecipientTypeDetails for seconds-to-minutes, D-99). `settle` polls such a verify
# instead of failing on the first stale read: it checks IMMEDIATELY (no latency when already
# consistent), then sleeps + retries while stale. Mirrors `_graph_common.settle`; `MSPAI_VERIFY_DELAY`
# (seconds) tunes the wait (tests set 0). Use ONLY for the genuinely-async ops, not ordinary attr sets.
_SETTLE_ATTEMPTS = 6
_SETTLE_DELAY = 2.0


def settle(read, ok, *, attempts=None, delay=None) -> "tuple[bool, Any]":
    """Poll read() until ok(result) is True; return (satisfied, last_result). Read exceptions are
    swallowed and retried. EXO conversions are slower than Graph, so the default is 6 × 2s."""
    import os
    import time
    attempts = _SETTLE_ATTEMPTS if attempts is None else attempts
    if delay is None:
        env = os.environ.get("MSPAI_VERIFY_DELAY")
        delay = float(env) if env not in (None, "") else _SETTLE_DELAY
    last: Any = None
    for i in range(attempts):
        try:
            last = read()
            if ok(last):
                return True, last
        except Exception:
            pass
        if i < attempts - 1:
            time.sleep(delay)
    return False, last


def is_not_found(e: str) -> bool:
    return bool(e) and ("NotFound" in e or "couldn't be found" in e)


def get_one_mailbox(exo, identity: str) -> tuple[Optional[dict], Optional[dict]]:
    """Resolve `identity` to exactly one mailbox. Returns (mailbox, None) or (None, error_dict)."""
    identity = (identity or "").strip()
    if not identity:
        return None, {"ok": False, "error": "no mailbox identity given"}
    r = exo.invoke("Get-Mailbox", {"Identity": identity})
    e = err(r)
    if e:
        if is_not_found(e):
            return None, {"ok": False, "error": f"no mailbox '{identity}' found"}
        return None, {"ok": False, "step": "preflight", "error": e}
    rows = [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]
    if len(rows) != 1:
        return None, {"ok": False, "error": f"'{identity}' matched {len(rows)} mailboxes — must "
                                            f"resolve to exactly one (use the primary address)"}
    return rows[0], None


# Set-Mailbox attributes that, for an AD-synced mailbox, are mastered ON-PREM and rejected by
# Exchange with a cryptic "out of the current user's write scope" 400 until the mailbox is flagged
# cloud-managed (IsExchangeCloudManaged). We pre-empt with a clear, actionable error that names the
# fix (exo_enable_cloud_management) instead of letting the write fail and reverse-engineering why
# afterward (D-91 follow-up).
_DIRECTORY_MASTERED = frozenset({
    "HiddenFromAddressListsEnabled",   # GAL / address-book visibility
    "EmailAddresses",                  # aliases / proxy addresses
    "WindowsEmailAddress",             # primary SMTP
    "MicrosoftOnlineServicesID",       # sign-in UPN
})


def needs_cloud_management(mb: dict, params: dict, *, label: str) -> Optional[dict]:
    """Return an actionable error dict iff `params` touch an on-prem-mastered attribute on a
    directory-synced mailbox that is NOT yet cloud-managed — else None. Callers run this right
    after their preflight Get-Mailbox, before Set-Mailbox, so the failure is explained up front
    and the agent can offer to enable cloud management instead of guessing post-mortem."""
    if not (_DIRECTORY_MASTERED & set(params or {})):
        return None
    if _norm(mb.get("IsDirSynced")) != "true" or _norm(mb.get("IsExchangeCloudManaged")) == "true":
        return None
    who = mb.get("PrimarySmtpAddress") or mb.get("DisplayName") or "this mailbox"
    return {"ok": False, "step": "preflight", "needs_cloud_management": True,
            "mailbox": mb.get("PrimarySmtpAddress"),
            "error": (f"can't {label}: {who} is a directory-synced mailbox and Exchange cloud "
                      f"management is NOT enabled, so this setting is still mastered by on-prem "
                      f"Active Directory and can't be changed in the cloud. Enable cloud "
                      f"management first (exo_enable_cloud_management), then retry — or change it "
                      f"from the on-prem directory.")}


def set_and_verify(exo, identity: str, params: dict, verify: dict[str, Any],
                   *, label: str) -> dict[str, Any]:
    """Set-Mailbox `params` on `identity`, then re-read and check every `verify` field
    landed (compared as case-insensitive strings — Exchange echoes many types as text).
    Never reports success it didn't observe."""
    mb, bad = get_one_mailbox(exo, identity)
    if bad:
        return bad
    guard = needs_cloud_management(mb, params, label=label)
    if guard:
        return guard
    r = exo.invoke("Set-Mailbox", {"Identity": identity, "Confirm": False, **params})
    if err(r):
        return {"ok": False, "step": label, "error": err(r)}
    after, bad = get_one_mailbox(exo, identity)
    if bad:
        return {"ok": False, "step": "verify",
                "error": f"{label}: Set-Mailbox returned no error but the re-read failed — "
                         f"{bad.get('error')}"}
    mismatched = {k: after.get(k) for k, want in verify.items()
                  if _norm(after.get(k)) != _norm(want)}
    if mismatched:
        return {"ok": False, "step": "verify", "expected": verify, "actual": mismatched,
                "error": f"{label}: the change did not stick — check Exchange directly"}
    return {"ok": True, "mailbox": after.get("PrimarySmtpAddress") or identity,
            "before": {k: mb.get(k) for k in verify}, "after": {k: after.get(k) for k in verify}}


def _norm(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).strip()
    # Exchange echoes sizes as "35 MB (36,700,160 bytes)" — compare the human part only,
    # space-insensitively, so it matches the "35MB" we sent.
    if " (" in s:
        s = s.split(" (", 1)[0]
    return s.replace(" ", "").lower()
