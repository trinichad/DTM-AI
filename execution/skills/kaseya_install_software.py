"""Install software on a machine via Chocolatey, through the run-command engine
(D-71; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_install_software"
DESCRIPTION = ("Install an application on a machine using Chocolatey (the install runs through "
               "the Command Toolkit engine). Pass the app — common names map to the right "
               "Chocolatey package (chrome, firefox, adobe reader, 7zip, zoom, etc.), or an exact "
               "Chocolatey package id — and `machine` for one box, or `machines` (a list) to "
               "install on MANY in ONE call — do NOT call this tool once per machine. Chocolatey "
               "is installed automatically if missing. Read progress with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "machines": {"type": "array", "items": {"type": "string"},
                     "description": "act on MANY machines in ONE call — a list of machine/agent "
                                    "names or AgentIds; results come back together. Use this "
                                    "instead of calling the tool once per machine."},
        "app": {"type": "string",
                "description": "app to install — a common name (chrome, firefox, adobe reader, "
                               "7zip, notepad++, zoom, vlc) or an exact Chocolatey package id"},
    },
    "required": ["app"],
    "additionalProperties": False,
}

# friendly name → Chocolatey package id (common ones; an unknown name is passed through as-is)
_CATALOG = {
    "chrome": "googlechrome", "google chrome": "googlechrome",
    "firefox": "firefox", "edge": "microsoft-edge",
    "adobe reader": "adobereader", "acrobat reader": "adobereader", "reader": "adobereader",
    "7zip": "7zip", "7-zip": "7zip",
    "notepad++": "notepadplusplus", "notepadplusplus": "notepadplusplus",
    "zoom": "zoom", "vlc": "vlc", "teams": "microsoft-teams",
    "java": "javaruntime", "dotnet": "dotnet-runtime",
}


def _package(app: str) -> str:
    a = (app or "").strip().lower()
    return _CATALOG.get(a, a.replace(" ", "-"))


def run(ctx, machine: str = "", machines: Any = None, app: str = "", **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = [_one(ctx, m, app) for m in wanted[:200]]
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine, app)


def _one(ctx, machine: str, app: str) -> dict:
    from . import _kaseya_common as k
    pkg = _package(app)
    if not pkg:
        return {"ok": False, "machine": machine, "error": "give an app to install"}
    # bootstrap Chocolatey if absent, then install the package silently
    command = (
        "if (!(Get-Command choco -ErrorAction SilentlyContinue)) { "
        "Set-ExecutionPolicy Bypass -Scope Process -Force; "
        "[System.Net.ServicePointManager]::SecurityProtocol = 3072; "
        "iex ((New-Object System.Net.WebClient).DownloadString("
        "'https://community.chocolatey.org/install.ps1')) }; "
        f"choco install {pkg} -y --no-progress"
    )
    out = k.run_command(ctx, machine, command)
    out.setdefault("machine", machine)
    if out.get("ok"):
        out["installing"] = pkg
        out["note"] = (f"installing '{pkg}' via Chocolatey — this can take a few minutes; "
                       f"check progress/result with kaseya_command_output")
    return out
