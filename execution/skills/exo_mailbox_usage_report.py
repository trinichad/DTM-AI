"""Tenant-wide mailbox usage / storage-triage report (D-114; SOP: exchange-online).

Loops EVERY mailbox in the bound client and reports, per mailbox: current size vs quota
(percent full), whether the online ARCHIVE is enabled (and its size), and the RETENTION POLICY
applied. Answers "which mailboxes are near full, and are they archiving / on what policy?" in ONE
call so the owner can decide a procedure (enable archive, set retention, etc.). Read-only.

Quota comes free from the Get-Mailbox listing (`ProhibitSendQuota`); only the SIZE needs a
per-mailbox Get-MailboxStatistics, so the scan is one stat call per mailbox (two if it has an
archive). Generic across all clients — the RHO retention mapping lives in client memory, not here.
"""
from __future__ import annotations

import re
from typing import Any, Optional

NAME = "exo_mailbox_usage_report"
DESCRIPTION = ("Tenant-wide mailbox STORAGE/triage report: for EVERY mailbox (or only those at/above "
               "a fullness threshold) report current size, mailbox quota, PERCENT FULL, whether the "
               "online ARCHIVE is enabled (and its size), and the RETENTION POLICY applied. Use for "
               "questions like 'which mailboxes are at 90%+ and do they have archiving on, and what "
               "retention policy?'. Pass `min_percent` to list only mailboxes at/above that % of "
               "quota (e.g. 90); `type` to filter (user/shared/room); `limit` to cap how many are "
               "scanned. Results are sorted fullest-first. Read-only; needs the client's Exchange "
               "connection — pick a specific client (it is per-client, not 'All clients').")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "min_percent": {"type": "number",
                        "description": "only include mailboxes at/above this percent of their quota "
                                       "(0-100), e.g. 90. Omit to report every mailbox."},
        "type": {"type": "string", "enum": ["all", "user", "shared", "room"],
                 "description": "filter by mailbox type (default all)"},
        "limit": {"type": "integer", "description": "max mailboxes to scan (default 1000, max 2000)"},
    },
    "additionalProperties": False,
}

_TYPES = {"shared": "SharedMailbox", "user": "UserMailbox", "room": "RoomMailbox"}
_NO_ARCHIVE_GUID = "00000000-0000-0000-0000-000000000000"
# EXO standard primary-mailbox quota — used ONLY to compute a percent when ProhibitSendQuota comes
# back "Unlimited"/DB-default with no explicit value; flagged with quota_assumed so it's honest.
_DEFAULT_QUOTA_BYTES = 100 * 1024 ** 3


def _bytes(val: Any) -> Optional[int]:
    """Pull the byte count out of an Exchange size string like '1.2 GB (1,288,490,188 bytes)'."""
    if val is None:
        return None
    m = re.search(r"\(([\d,]+)\s*bytes\)", str(val))
    return int(m.group(1).replace(",", "")) if m else None


def _stat_size(exo, identity: str, archive: bool) -> tuple[Any, Optional[int], str]:
    from . import _exo_common as c
    params: dict[str, Any] = {"Identity": identity}
    if archive:
        params["Archive"] = True
    r = exo.invoke("Get-MailboxStatistics", params)
    if c.err(r):
        return None, None, c.err(r)
    row = r[0] if isinstance(r, list) and r else r
    if not isinstance(row, dict):
        return None, None, "no statistics returned"
    size = row.get("TotalItemSize")
    return size, _bytes(size), ""


def _row(exo, mb: dict) -> dict:
    primary = str(mb.get("PrimarySmtpAddress") or mb.get("Identity") or "")
    archive_guid = str(mb.get("ArchiveGuid") or "")
    has_archive = (bool(archive_guid) and archive_guid != _NO_ARCHIVE_GUID
                   and str(mb.get("ArchiveState")) != "None")

    quota_raw = mb.get("ProhibitSendQuota")
    quota_bytes = _bytes(quota_raw)
    quota_assumed = quota_bytes is None
    if quota_assumed:
        quota_bytes = _DEFAULT_QUOTA_BYTES

    size_raw, size_bytes, serr = _stat_size(exo, primary, archive=False)
    pct = round(size_bytes / quota_bytes * 100, 1) if (size_bytes and quota_bytes) else None

    row: dict[str, Any] = {
        "mailbox": primary,
        "display_name": mb.get("DisplayName"),
        "type": mb.get("RecipientTypeDetails"),
        "size": size_raw,
        "quota": (f"{_DEFAULT_QUOTA_BYTES // 1024 ** 3} GB (assumed EXO default)"
                  if quota_assumed else quota_raw),
        "percent_used": pct,
        "archive": "enabled" if has_archive else "disabled",
        "retention_policy": mb.get("RetentionPolicy"),
    }
    if quota_assumed:
        row["quota_assumed"] = True
    if serr:
        row["size_error"] = serr
    if has_archive:
        a_raw, _a_bytes, _ = _stat_size(exo, primary, archive=True)
        row["archive_usage"] = a_raw
    return row


def run(ctx, min_percent: Any = None, type: str = "all", limit: int = 1000, **_: Any):
    from . import _exo_common as c
    exo = ctx.client("exo")
    try:
        limit = max(1, min(int(limit or 1000), 2000))
    except (TypeError, ValueError):
        limit = 1000
    params: dict[str, Any] = {"ResultSize": limit}
    details = _TYPES.get((type or "all").strip().lower())
    if details:
        params["RecipientTypeDetails"] = details

    r = exo.invoke("Get-Mailbox", params)
    if c.err(r):
        return {"ok": False, "error": c.err(r)}
    boxes = [m for m in (r if isinstance(r, list) else [r]) if isinstance(m, dict)]

    rows = ctx.map_progress(boxes, lambda m: _row(exo, m),
                            label=lambda m: str(m.get("PrimarySmtpAddress") or ""))

    try:
        threshold = float(min_percent) if min_percent is not None else None
    except (TypeError, ValueError):
        threshold = None
    if threshold is not None:
        rows = [x for x in rows if x.get("percent_used") is not None
                and x["percent_used"] >= threshold]
    # fullest first; mailboxes whose size couldn't be read (percent None) sort last
    rows.sort(key=lambda x: (x.get("percent_used") is None, -(x.get("percent_used") or 0)))

    out: dict[str, Any] = {"ok": True, "scanned": len(boxes), "count": len(rows), "mailboxes": rows}
    if threshold is not None:
        out["min_percent"] = threshold
    return out
