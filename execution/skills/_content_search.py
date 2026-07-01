"""Shared plumbing for the Content Search skills (D-115; SOP: exchange-online).

No NAME attribute → the registry skips this module (I-1); it is a library, not a tool.

Two jobs:
  - build_kql(): assemble a Content Search KQL string from friendly fields, keeping WHERE
    (mailbox locations) separate from WHO (from:/to:/participants: inside the query).
  - parse_*(): the compliance endpoint returns SuccessResults (per-mailbox stats) and preview
    Results (item rows) as semicolon/comma-delimited STRINGS whose values (subjects) can contain
    the delimiters. We parse best-effort by anchoring each record at "Location:" and stopping each
    field at the next KNOWN key — and callers ALWAYS surface the raw string too, so a format drift
    degrades to "raw shown, rows empty", never to silent data loss.
"""
from __future__ import annotations

import re
from typing import Any, Optional


def rows(r: Any) -> list[dict]:
    """Normalize an invoke_compliance result into a list of dict records."""
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def role_hint(err: str) -> str:
    """Append an actionable hint when a compliance call is refused for lack of an eDiscovery role."""
    e = (err or "").lower()
    if "401" in e or "403" in e or "denied" in e or "unauthorized" in e:
        return (" — the signed-in admin needs the eDiscovery Manager role (or the Compliance "
                "Search / Preview management roles) in the Purview portal")
    return ""


# ── KQL assembly ──────────────────────────────────────────────────────────────────────────────
def _q(v: str) -> str:
    """KQL has no clean escape for an embedded double-quote, so we strip them (safe for the
    addresses/subjects these tools accept)."""
    return str(v or "").replace('"', "").strip()


def build_kql(*, keywords: str = "", from_address: str = "", to_address: str = "",
              participants: str = "", subject: str = "", date_from: str = "", date_to: str = "",
              has_attachment: Optional[bool] = None, raw_kql: str = "") -> str:
    """Assemble a ContentMatchQuery. A non-empty `raw_kql` is used verbatim (escape hatch).
    Returns "" when nothing was specified — callers treat empty as "match ALL" and refuse it."""
    raw = (raw_kql or "").strip()
    if raw:
        return raw
    parts: list[str] = []
    kw = (keywords or "").strip()
    if kw:
        parts.append(kw)                                   # caller controls AND/OR/NOT/quoting here
    if _q(from_address):
        parts.append(f'from:"{_q(from_address)}"')
    if _q(to_address):
        parts.append(f'to:"{_q(to_address)}"')
    if _q(participants):
        parts.append(f'participants:"{_q(participants)}"')  # sender OR any recipient
    if _q(subject):
        parts.append(f'subject:"{_q(subject)}"')
    df, dt = _q(date_from), _q(date_to)
    if df:
        parts.append(f"received>={df}")
    if dt:
        parts.append(f"received<={dt}")
    if has_attachment is not None:
        parts.append(f"hasattachment:{'true' if has_attachment else 'false'}")
    return " AND ".join(parts)


# ── result-string parsing ───────────────────────────────────────────────────────────────────────
def _int(s: Any) -> Optional[int]:
    m = re.search(r"-?\d[\d,]*", str(s if s is not None else ""))
    return int(m.group(0).replace(",", "")) if m else None


def human_size(n: Any) -> Optional[str]:
    try:
        size = float(n)
    except (TypeError, ValueError):
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return None


def _records(s: str) -> list[str]:
    """Split a delimited result string into one chunk per item — each item begins with 'Location:'."""
    s = s or ""
    starts = [m.start() for m in re.finditer(r"Location:\s", s)]
    if not starts:
        return []
    starts.append(len(s))
    return [s[starts[i]:starts[i + 1]].strip(" ;,\n") for i in range(len(starts) - 1)]


def _field(rec: str, key: str, keys: tuple[str, ...]) -> str:
    """Value of `key` in one record — runs until the next KNOWN key (so a ';' or ',' inside a
    subject doesn't truncate it) or end of record."""
    stops = "|".join(re.escape(k) for k in keys if k != key)
    tail = (rf"\s*[;,]\s*(?:{stops}):" if stops else "") + r"|\s*$"
    m = re.search(re.escape(key) + r":\s*(.*?)(?:" + tail + r")", rec, re.DOTALL)
    return m.group(1).strip().rstrip(";,").strip() if m else ""


def parse_export_credentials(results: Any) -> tuple[Optional[str], Optional[str]]:
    """Pull (container_url, sas_token) from a `<name>_Export` action's IncludeCredential Results
    string. Defensive: matches the labelled fields first, then falls back to scanning for an Azure
    blob URL and an `sv=`-style SAS so a label change doesn't break us. Returns (None, None) if
    either piece is missing — the SAS is a credential and never logged/returned beyond here."""
    s = str(results or "")
    url = None
    m = re.search(r"Container url:\s*(\S+)", s, re.I)
    if m:
        url = m.group(1).rstrip(";,")
    else:
        m = re.search(r"https://[^\s;,]+\.blob\.core\.windows\.net/[^\s;,]+", s, re.I)
        url = m.group(0) if m else None
    sas = None
    m = re.search(r"SAS token:\s*(\S+)", s, re.I)
    if m:
        sas = m.group(1).rstrip(";,")
    else:
        m = re.search(r"\??(sv=[^\s;,]+)", s, re.I)   # SAS query string begins with sv=<version>
        sas = m.group(1) if m else None
    return url, sas


_LOC_KEYS = ("Location", "Item count", "Total size")
_ITEM_KEYS = ("Location", "Sender", "Subject", "Type", "Size", "Received Time", "Data Link")


def parse_location_stats(success_results: Any) -> list[dict]:
    """Per-mailbox item/size breakdown from a search's SuccessResults string."""
    out = []
    for rec in _records(str(success_results or "")):
        loc = _field(rec, "Location", _LOC_KEYS)
        if loc:
            out.append({"mailbox": loc,
                        "items": _int(_field(rec, "Item count", _LOC_KEYS)),
                        "size_bytes": _int(_field(rec, "Total size", _LOC_KEYS))})
    return out


def parse_preview(results: Any, limit: int = 200) -> list[dict]:
    """Sample item rows from a preview action's Results string."""
    out = []
    for rec in _records(str(results or "")):
        row = {k: _field(rec, k, _ITEM_KEYS) for k in _ITEM_KEYS}
        if any(row.values()):
            out.append({"mailbox": row["Location"], "sender": row["Sender"],
                        "subject": row["Subject"], "type": row["Type"],
                        "size_bytes": _int(row["Size"]), "received": row["Received Time"]})
        if len(out) >= limit:
            break
    return out
