"""Shared plumbing for the Kaseya VSA read skills (D-68; SOP: kaseya-vsa).

No NAME attribute → invisible to the registry (I-1). The Kaseya REST envelope is
`{TotalRecords, Result, ResponseCode, Status, Error}`; `result()` unwraps `.Result` and turns a
non-empty `.Error` into a clean failure. `resolve_agent()` maps a machine name or AgentId to one
agent (refusing an ambiguous match), since per-agent audit endpoints key on the numeric AgentId.
"""
from __future__ import annotations

import re
import secrets
import string
from typing import Any, Optional

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def ps_quote(value: str) -> str:
    """Wrap a value as a PowerShell SINGLE-quoted string with embedded quotes doubled. Single-
    quoted strings interpret nothing except '' → ', so this is injection-safe for ANY content
    (no $, backtick, ; can break out) — used by the AD tools that embed names/passwords (D-72)."""
    return "'" + str(value).replace("'", "''") + "'"


def clean_text(value: str, maxlen: int = 256) -> Optional[str]:
    """Trim + reject empty / control chars / newlines / over-length. None if invalid."""
    s = str(value or "").strip()
    if not s or _CTRL.search(s) or len(s) > maxlen:
        return None
    return s


def gen_password(n: int = 16) -> str:
    """A strong password with all four classes (server-side; never the model's)."""
    pools = (string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%*")
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice("".join(pools)) for _ in range(max(4, n) - 4)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


# ── Network validators (D-76, DHCP tools) — keep user-supplied addresses well-formed before they
# go into a (ps_quote'd, so already injection-safe) command, for clean errors and a sane command.
_IPV4_RE = re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$")
_HEX12_RE = re.compile(r"^[0-9A-Fa-f]{12}$")


def is_ipv4(value: str) -> bool:
    """True for a dotted-quad IPv4 address (each octet 0-255)."""
    return bool(_IPV4_RE.match(str(value or "").strip()))


def clean_mac(value: str) -> Optional[str]:
    """Normalize a MAC / DHCP ClientId to 'aa-bb-cc-dd-ee-ff' (12 hex digits). None if invalid."""
    raw = re.sub(r"[\s:.-]", "", str(value or ""))
    if not _HEX12_RE.match(raw):
        return None
    raw = raw.lower()
    return "-".join(raw[i:i + 2] for i in range(0, 12, 2))


# ── SSH-to-network-gear helper (D-85) — UniFi field tools run plink (PuTTY) from a Windows machine
# on the CLIENT LAN via the Kaseya command engine. Windows' built-in ssh can't take a password
# non-interactively, so we use plink -pw. Every value is injected as a ps_quote'd single-quoted
# literal → injection-impossible. The host key is auto-cached by piping "y" to the first connection.
_PLINK_PREAMBLE = (
    "$plink = (Get-Command plink.exe -ErrorAction SilentlyContinue).Source; "
    "if (-not $plink) { if (Get-Command choco -ErrorAction SilentlyContinue) { try { "
    "choco install putty -y --no-progress --limit-output | Out-Null } catch {} } }; "
    "if (-not $plink) { foreach ($p in @('C:\\ProgramData\\chocolatey\\bin\\plink.exe',"
    "'C:\\Program Files\\PuTTY\\plink.exe')) { if (Test-Path $p) { $plink = $p; break } } }; "
    "if (-not $plink) { $plink = (Get-Command plink.exe -ErrorAction SilentlyContinue).Source }; "
    "if (-not $plink) { throw 'plink (PuTTY) is not installed and could not be auto-installed via "
    "Chocolatey — install PuTTY on this machine and retry' }; "
)


def ssh_command_ps(user: str, ip: str, password: str, remote_cmd: str) -> str:
    """Full try/catch PowerShell that SSHes into `ip` as `user` and runs `remote_cmd`, returning the
    output. Injection-safe (user/ip/password/remote_cmd all ps_quote'd)."""
    return ("try { " + _PLINK_PREAMBLE +
            "$pw = " + ps_quote(password) + "; "
            "$dest = " + ps_quote(f"{user}@{ip}") + "; "
            "$remote = " + ps_quote(remote_cmd) + "; "
            "(\"y`n\" | & $plink -ssh -pw $pw $dest $remote 2>&1 | Out-String) } "
            "catch { 'ERROR: ' + $_.Exception.Message }")


# ── Windows registry helpers (D-80) — normalize a hive path to a provider-qualified PS path
# (works for ALL hives without needing a PSDrive) and render a typed value for -Value. The full
# path is ps_quote'd by the caller, so injection is impossible regardless of contents.
_HIVES = {
    "HKLM": "HKEY_LOCAL_MACHINE", "HKEY_LOCAL_MACHINE": "HKEY_LOCAL_MACHINE",
    "HKCU": "HKEY_CURRENT_USER", "HKEY_CURRENT_USER": "HKEY_CURRENT_USER",
    "HKCR": "HKEY_CLASSES_ROOT", "HKEY_CLASSES_ROOT": "HKEY_CLASSES_ROOT",
    "HKU": "HKEY_USERS", "HKEY_USERS": "HKEY_USERS",
    "HKCC": "HKEY_CURRENT_CONFIG", "HKEY_CURRENT_CONFIG": "HKEY_CURRENT_CONFIG",
}
_REG_TYPES = {"string": "String", "expandstring": "ExpandString", "dword": "DWord",
              "qword": "QWord", "multistring": "MultiString"}


def reg_path(key: str):
    """'HKLM\\Software\\X' (or 'HKLM:\\…') → ('Registry::HKEY_LOCAL_MACHINE\\Software\\X', None),
    or (None, error). Hive must be one of HKLM/HKCU/HKCR/HKU/HKCC."""
    s = str(key or "").strip().replace("/", "\\")
    head, _, tail = s.partition("\\")
    full = _HIVES.get(head.rstrip(":").upper())
    if not full:
        return None, "key must start with a hive: HKLM, HKCU, HKCR, HKU, or HKCC"
    sub = ""
    if tail:
        sub = clean_text(tail, 512)
        if sub is None:
            return None, "the registry path is not valid"
    return "Registry::" + full + ("\\" + sub if sub else ""), None


def reg_type_value(rtype: str, value, values):
    """(ps_type, rendered_value, None) or (None, None, error). rendered_value is a PS literal:
    a number for DWord/QWord, a quoted string, or @('a','b') for MultiString."""
    tp = _REG_TYPES.get(str(rtype or "").strip().lower())
    if not tp:
        return None, None, "type must be one of: " + ", ".join(_REG_TYPES)
    if tp in ("DWord", "QWord"):
        try:
            num = int(str(value).strip(), 0)            # accepts decimal and 0x-hex
        except (TypeError, ValueError):
            return None, None, f"{tp} needs a whole-number value"
        return tp, str(num), None
    if tp == "MultiString":
        items = values if isinstance(values, list) else ([value] if str(value or "").strip() else [])
        if not items:
            return None, None, "multistring needs `values` (a list of strings)"
        return tp, _ps_value([str(x) for x in items]), None
    v = clean_text(value, 1024)
    if v is None:
        return None, None, "give a valid string value"
    return tp, ps_quote(v), None


# ── AD user properties (D-72 follow-up) — friendly param → New-ADUser/Set-ADUser parameter.
# Both cmdlets share these parameter names, so the same builder works for create and modify.
AD_PROPS: dict[str, str] = {
    "display_name": "DisplayName", "email": "EmailAddress", "upn": "UserPrincipalName",
    "title": "Title", "department": "Department", "company": "Company", "office": "Office",
    "description": "Description", "office_phone": "OfficePhone", "mobile_phone": "MobilePhone",
    "fax": "Fax", "home_phone": "HomePhone",
    "street": "StreetAddress", "city": "City", "state": "State", "postal_code": "PostalCode",
    "country": "Country", "manager": "Manager", "script_path": "ScriptPath",
}
_AD_PROP_HELP = {
    "display_name": "display name", "email": "email address", "upn": "userPrincipalName",
    "title": "job title", "department": "department", "company": "company",
    "office": "office location", "description": "description", "office_phone": "office phone",
    "mobile_phone": "mobile phone", "fax": "fax", "home_phone": "home phone",
    "street": "street address", "city": "city", "state": "state/province",
    "postal_code": "zip/postal code", "country": "country (2-letter code, e.g. US)",
    "manager": "manager (sAMAccountName, name, or DN)", "script_path": "logon script path",
}
# JSON-schema fragment to spread into a tool's PARAMETERS["properties"]
AD_PROP_SCHEMA: dict[str, Any] = {
    key: {"type": "string", "description": f"{help} (optional)"}
    for key, help in _AD_PROP_HELP.items()
}


def ad_property_fragments(values: dict):
    """Build `-Param 'value'` command fragments for the AD_PROPS that are present + non-empty.
    Returns (list_of_fragments, None) or ([], error_string)."""
    frags: list[str] = []
    for key, param in AD_PROPS.items():
        raw = values.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        v = clean_text(raw, 512)
        if not v:
            return [], f"{key} is not a valid value"
        if key == "country" and len(v) != 2:
            return [], "country must be a 2-letter code (e.g. US)"
        if key == "email" and "@" not in v:
            return [], f"'{raw}' is not a valid email"
        frags += [f"-{param}", ps_quote(v)]
    return frags, None


def _ps_value(v) -> str:
    if isinstance(v, list):
        return "@(" + ",".join(ps_quote(str(x)) for x in v) + ")"
    return ps_quote(str(v))


def ad_hashtable(d: dict):
    """A PowerShell hashtable literal for Set-ADUser -Add/-Remove/-Replace, e.g.
    @{ 'proxyAddresses'='smtp:a@x' }. Attribute names validated; values quoted. (ht, None)/(None, err)."""
    items = []
    for name, val in d.items():
        if not re.match(r"^[A-Za-z0-9-]+$", str(name)):
            return None, f"invalid attribute name '{name}'"
        items.append(f"{ps_quote(str(name))}={_ps_value(val)}")
    return "@{ " + "; ".join(items) + " }", None

_NAME_FIELDS = ("AgentName", "ComputerName", "DisplayName", "AssetName", "MachineGroup")


def result(client, path: str, params: Optional[dict] = None):
    """GET an endpoint → (data, None) or (None, error_string). `data` is the unwrapped `.Result`
    (list or dict), or the raw body if there is no Result key."""
    from execution.clients._http import HttpError
    try:
        data = client.get(path, params)
    except HttpError as e:
        if e.status == 403:                      # 403/4030001 on audit reads = a Role-rights gap,
            return None, (                       # not a bad URL — name the fix (D-68 lesson)
                "Access denied (HTTP 403) — the Kaseya API user's Role lacks rights to read this "
                "data. In the VSA console grant that user's Role the relevant function access "
                "(e.g. Audit) and a Scope covering the machine's group (System > User Security > "
                "Roles / Scopes), then retry.")
        return None, f"Kaseya HTTP {e.status}: {str(e.body)[:200]}"
    except Exception as e:                       # noqa: BLE001 — surface as data, never raise
        return None, f"{type(e).__name__}: {e}"
    if data is None:
        return None, f"no response from {path}"
    if isinstance(data, dict):
        if _envelope_error(data):
            return None, str(data["Error"])
        return (data.get("Result") if "Result" in data else data), None
    return data, None


def _envelope_error(data: dict) -> bool:
    """True only for a REAL Kaseya error. VSA 9 returns the literal string `"Error": "None"` on
    SUCCESS (alongside ResponseCode 0 / Status "OK"), so a truthy Error field is NOT enough — treat
    "none"/"null"/empty as success."""
    err = data.get("Error")
    return bool(err) and str(err).strip().lower() not in ("", "none", "null")


def rows(data: Any) -> list[dict]:
    """Normalize a Result into a list of dict rows."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return [data] if isinstance(data, dict) else []


def resolve_agent(client, needle: str):
    """Find ONE agent by AgentId or a case-insensitive name substring.
    Returns (agent_dict, None) or (None, error_string)."""
    needle = str(needle or "").strip()
    if not needle:
        return None, "give a machine name or AgentId"
    try:
        agents = client.get_agents()
    except Exception as e:                       # noqa: BLE001
        return None, f"could not list Kaseya agents: {e}"
    for a in agents:                             # exact id wins
        if str(a.get("AgentId")) == needle:
            return a, None
    low = needle.lower()
    hits = [a for a in agents
            if low in " ".join(str(a.get(k, "")) for k in _NAME_FIELDS).lower()]
    if not hits:
        return None, f"no Kaseya agent matched '{needle}'"
    if len(hits) > 1:
        names = [str(a.get("AgentName") or a.get("ComputerName") or a.get("AgentId"))
                 for a in hits[:6]]
        return None, (f"'{needle}' matched {len(hits)} agents — be more specific: "
                      f"{', '.join(names)}")
    return hits[0], None


def slim(row: dict, fields: tuple) -> dict:
    """Keep known fields; fall back to the raw row so a version-shifted shape is never nulled out."""
    picked = {k: row[k] for k in fields if k in row}
    return picked or row


_PROC_ID = ("AgentProcedureId", "ProcedureId", "Id")
_PROC_NAME = ("AgentProcedureName", "ProcedureName", "Name", "ScriptName")


def resolve_procedure(client, needle: str):
    """Map an agent-procedure name or id to its id. Returns (proc_id, proc_name, None) or
    (None, None, error_string). Refuses an ambiguous name (with candidates)."""
    needle = str(needle or "").strip()
    if not needle:
        return None, None, "give an agent-procedure name or id"
    data, err = result(client, "/automation/agentprocs")
    if err:
        return None, None, f"could not list agent procedures: {err}"
    procs = rows(data)

    def pid(p):
        return next((p[k] for k in _PROC_ID if p.get(k) is not None), None)

    def pname(p):
        return next((p[k] for k in _PROC_NAME if p.get(k) is not None), "")

    for p in procs:                              # exact id wins
        if str(pid(p)) == needle:
            return pid(p), pname(p), None
    low = needle.lower()
    hits = [p for p in procs if low == str(pname(p)).lower()] \
        or [p for p in procs if low in str(pname(p)).lower()]
    if not hits:
        return None, None, f"no agent procedure matched '{needle}'"
    if len(hits) > 1:
        names = [str(pname(p)) for p in hits[:6]]
        return None, None, (f"'{needle}' matched {len(hits)} procedures — be specific: "
                            f"{', '.join(names)}")
    return pid(hits[0]), pname(hits[0]), None


# Shared command-engine entry point (D-70/D-71). Every tool that runs a command on an endpoint
# (kaseya_run_command + the named IT tools) routes through here: resolve the owner's one
# "run command" procedure and schedule it to run NOW with the command as a prompt value.
def run_command(ctx, machine: str, command: str, *, power_up_if_offline: bool = False):
    """Returns the uniform tool result dict (ok/error + machine/agent_id/command/note)."""
    from execution.core.config import get_config
    command = (command or "").strip()
    if not command:
        return {"ok": False, "error": "no command to run"}
    proc_name = (get_config().get("KASEYA_RUN_COMMAND_PROCEDURE")
                 or "MSP AI Run Command").strip()
    client = ctx.client("kaseya")
    agent, err = resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")
    pid, pname, perr = resolve_procedure(client, proc_name)
    if perr:
        return {"ok": False, "error":
                f"the command-runner procedure '{proc_name}' wasn't found in Kaseya — create it "
                f"in the Kaseya console (Capabilities → Command Toolkit → Setup) or set the "
                f"KASEYA_RUN_COMMAND_PROCEDURE config to your procedure's name. ({perr})"}
    body = {"SkipIfOffLine": False, "PowerUpIfOffLine": bool(power_up_if_offline),
            "ScriptPrompts": [{"Caption": "command", "Name": "command", "Value": command}],
            "Recurrence": {"Repeat": "Never"}, "Distribution": {}, "Start": {}}
    r = client.write("PUT", f"/automation/agentprocs/{aid}/{pid}/schedule", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "command": command, "procedure": pname,
            "note": "command submitted — runs when the agent next checks in (seconds if online); "
                    "read the result with kaseya_command_output once it completes."}
