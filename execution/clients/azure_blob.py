"""Minimal Azure Blob container reader for Content Search export download (D-116).

A Purview Content Search export stages its results in an Azure blob container behind a
short-lived SAS URL. There is no azcopy/az on the box, so we read the container directly over
the Blob REST API with the stdlib HTTP helpers:
  - List Blobs:  GET {container}?restype=container&comp=list&{sas}      → XML enumeration
  - Get Blob:    GET {container}/{blob}?{sas}                           → raw bytes

Security posture (the SAS is a live bearer credential — D-1/I-3): this stays server-side; the
token is NEVER returned to a caller or the browser. Egress is fail-closed to Azure blob hosts
only, and total download is capped (an oversized export aborts rather than filling the disk).
Transports are injectable so tests need no network.
"""
from __future__ import annotations

import os
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

from ._http import download_to_file, http_bytes

# Only ever fetch from Azure blob storage hosts (commercial cloud). Government/other clouds can
# be added deliberately if a client needs them — fail closed on anything else.
_ALLOWED_HOST_SUFFIXES = (".blob.core.windows.net",)


def _check_host(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if not host or not any(host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES):
        raise ValueError(f"refusing to fetch from non-Azure-blob host '{host or url}'")
    return host


def _sas(token: str) -> str:
    return (token or "").lstrip("?")


def _list_url(container_url: str, token: str, marker: Optional[str]) -> str:
    extra = {"restype": "container", "comp": "list"}
    if marker:
        extra["marker"] = marker
    return f"{container_url}?{urllib.parse.urlencode(extra)}&{_sas(token)}"


def _blob_url(container_url: str, blob_name: str, token: str) -> str:
    # quote the blob path but keep '/' so nested folders stay intact
    return f"{container_url.rstrip('/')}/{urllib.parse.quote(blob_name)}?{_sas(token)}"


def _safe_rel(name: str) -> str:
    """Map a blob name to a safe relative path (no leading slash, no '..' escape)."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return os.path.join(*parts) if parts else "blob.bin"


def list_blobs(container_url: str, token: str, *,
               transport: Callable = http_bytes) -> list[dict]:
    """Enumerate every blob in the container (follows NextMarker pagination)."""
    _check_host(container_url)
    out: list[dict] = []
    marker: Optional[str] = None
    for _ in range(1000):                                  # pagination backstop
        _status, raw = transport("GET", _list_url(container_url, token, marker))
        root = ET.fromstring(raw)
        for b in root.iter("Blob"):
            name = b.findtext("Name")
            size = b.findtext("Properties/Content-Length")
            if name:
                out.append({"name": name,
                            "size": int(size) if (size or "").isdigit() else None})
        marker = (root.findtext("NextMarker") or "").strip() or None
        if not marker:
            break
    return out


def download_container(container_url: str, token: str, dest_dir: str, *, max_bytes: int,
                       on_progress: Optional[Callable[[int, int, str], None]] = None,
                       lister: Callable = list_blobs,
                       downloader: Callable = download_to_file) -> dict:
    """Download every blob into `dest_dir`, preserving its folder structure. Enforces a TOTAL
    `max_bytes` cap — pre-checks the listing's declared sizes, then re-checks live per blob.
    Returns a manifest; raises ValueError if the declared total is over the cap."""
    _check_host(container_url)
    blobs = lister(container_url, token)
    declared = sum(b["size"] or 0 for b in blobs)
    if declared and declared > max_bytes:
        raise ValueError(f"export is ~{declared} bytes, over the {max_bytes}-byte in-app cap")

    os.makedirs(dest_dir, exist_ok=True)
    files: list[dict] = []
    total = 0
    n = len(blobs)
    for i, b in enumerate(blobs):
        rel = _safe_rel(b["name"])
        dest = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(dest) or dest_dir, exist_ok=True)
        got = downloader("GET", _blob_url(container_url, b["name"], token),
                         dest_path=dest, max_bytes=max(0, max_bytes - total))
        total += got
        files.append({"name": b["name"], "path": dest, "bytes": got})
        if on_progress:
            on_progress(i + 1, n, b["name"])
    return {"dir": dest_dir, "blob_count": n, "total_bytes": total, "files": files}
