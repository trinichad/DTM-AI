"""Export a Content Search and download the results into the dashboard (D-116; SOP: exchange-online).

Phase 2 of Content Search. Idempotent lifecycle on the Security & Compliance endpoint, mirroring
preview, plus a server-side blob download (Option B — the owner chose backend-pull over the
ClickOnce eDiscovery Export Tool):

  - no <name>_Export action yet + search Completed → New-ComplianceSearchAction -Export.
  - export action exists but not Completed         → "still preparing".
  - export action Completed                        → Get-ComplianceSearchAction -IncludeCredential,
      read the staging container's SAS URL, download every blob server-side (capped + streamed),
      land it under the vault's exports/ dir, and return a dashboard download link.

The SAS URL is a live bearer credential to the exported mail — it stays server-side (D-1/I-3) and is
NEVER returned to the caller or the browser. Download is capped (MSPAI_CONTENT_EXPORT_MAX_GB, default
2 GB); an over-cap export aborts with an honest message rather than filling the disk. -Purge is
unreachable (the cmdlet param allowlist forbids it).
"""
from __future__ import annotations

import os
import re
from typing import Any

NAME = "exo_content_search_export"
DESCRIPTION = (
    "Export a completed Microsoft Purview Content Search and download the results into the "
    "dashboard. Pass the search `name`. Idempotent: the first call starts the export, and calling "
    "it again once it finishes downloads the results server-side (no SAS token to paste, no "
    "eDiscovery Export Tool) and returns a download link. The search must have finished estimating "
    "first (see exo_content_search_status). Large exports are capped; very large ones are refused "
    "with guidance. Requires the eDiscovery Manager / Export role on the signed-in admin.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"            # exports other people's mail content out of the tenant
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "the content search name to export"},
    },
    "required": ["name"],
    "additionalProperties": False,
}

_DEFAULT_MAX_GB = 2.0


def run(ctx, name: str = "", **_: Any):
    from . import _content_search as cs
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "give the content search `name` to export"}
    exo = ctx.client("exo")
    action_id = f"{name}_Export"

    # 1. does an export action already exist?
    existing = exo.invoke_compliance("Get-ComplianceSearchAction",
                                     {"Identity": action_id, "Details": True,
                                      "IncludeCredential": True})
    e = _err(existing)
    if e and not _not_found(e):
        return {"ok": False, "step": "read", "error": e + cs.role_hint(e)}
    act = cs.rows(existing) if not e else []
    if act:
        status = str(act[0].get("Status") or "")
        if status.lower() != "completed":
            return {"ok": True, "name": name, "export_status": status or "InProgress",
                    "note": "export is still being prepared — run exo_content_search_export again "
                            "in a few minutes"}
        return _download(ctx, cs, name, act[0])

    # 2. no export yet — the search must have finished estimating first
    raw = exo.invoke_compliance("Get-ComplianceSearch", {"Identity": name})
    srch = [] if _err(raw) else cs.rows(raw)
    if not srch:
        return {"ok": False, "step": "locate",
                "error": f"no content search named '{name}' — create one with "
                         f"exo_content_search_create"}
    sstatus = str(srch[0].get("Status") or "")
    if sstatus.lower() != "completed":
        return {"ok": True, "name": name, "search_status": sstatus or "InProgress",
                "note": f"the search estimate isn't finished yet (status: "
                        f"{sstatus or 'InProgress'}) — wait, then run export again"}

    # 3. start the export
    r = exo.invoke_compliance("New-ComplianceSearchAction", {"SearchName": name, "Export": True})
    se = _err(r)
    if se:
        return {"ok": False, "step": "start_export", "error": se + cs.role_hint(se)}
    return {"ok": True, "name": name, "export_status": "Starting",
            "note": "export started — run exo_content_search_export again in a few minutes to "
                    "download the results"}


def _download(ctx, cs, name: str, action: dict) -> dict:
    """Export action is Completed: read its SAS URL and pull the blobs server-side."""
    from ..clients import azure_blob
    from ..clients._http import DownloadTooLarge

    container, sas = cs.parse_export_credentials(action.get("Results"))
    if not container or not sas:
        return {"ok": False, "step": "credentials", "name": name,
                "error": "the export finished but its download credentials could not be read "
                         "(Get-ComplianceSearchAction -IncludeCredential returned no container "
                         "URL / SAS) — check the search in the Purview portal"}

    dest_dir = os.path.join(_exports_root(ctx), _safe(name))
    max_bytes = int(_max_gb() * (1024 ** 3))
    try:
        manifest = azure_blob.download_container(
            container, sas, dest_dir, max_bytes=max_bytes,
            on_progress=lambda i, n, blob: ctx.progress(i, n, blob.split("/")[-1]))
    except ValueError as exc:                              # declared total over the cap
        return {"ok": False, "step": "download", "name": name, "too_large": True,
                "error": f"{exc}. Raise MSPAI_CONTENT_EXPORT_MAX_GB (currently "
                         f"{_max_gb():g} GB) or narrow the search (tighter dates/mailboxes), "
                         f"then export again."}
    except DownloadTooLarge:
        return {"ok": False, "step": "download", "name": name, "too_large": True,
                "error": f"the export exceeded the {_max_gb():g} GB in-app cap mid-download — "
                         f"raise MSPAI_CONTENT_EXPORT_MAX_GB or narrow the search."}
    except Exception as exc:                               # network / blob error — fail closed, honest
        return {"ok": False, "step": "download", "name": name,
                "error": f"downloading the export failed: {str(exc)[:300]}"}

    return {"ok": True, "name": name, "export_status": "Completed",
            "files": manifest["blob_count"], "total_bytes": manifest["total_bytes"],
            "size": cs.human_size(manifest["total_bytes"]),
            "download_dir": manifest["dir"],
            "download_url": f"/api/fs/download?path={_urlq(manifest['dir'])}",
            "note": "results downloaded to the dashboard exports folder; open the download link "
                    "(admin-gated). The download contains the exported mail — handle accordingly."}


def _exports_root(ctx) -> str:
    from ..core.config import _PROJECT_ROOT, get_config
    cfg = get_config()
    vault = cfg.get("MSPAI_VAULT_PATH") or str(_PROJECT_ROOT / "vault")
    return os.path.join(vault, "exports", _safe(ctx.tenant_id or "unknown"))


def _max_gb() -> float:
    from ..core.config import get_config
    raw = get_config().get("MSPAI_CONTENT_EXPORT_MAX_GB")
    try:
        return float(raw) if raw not in (None, "") else _DEFAULT_MAX_GB
    except (TypeError, ValueError):
        return _DEFAULT_MAX_GB


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s or "")).strip("._") or "x"


def _urlq(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s, safe="")


def _err(r: Any) -> str:
    return str(r.get("error")) if isinstance(r, dict) and r.get("error") else ""


def _not_found(e: str) -> bool:
    e = (e or "").lower()
    return any(s in e for s in ("not found", "couldn't be found", "wasn't found", "does not exist"))
