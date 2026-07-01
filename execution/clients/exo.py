"""Exchange Online client (D-41) — InvokeCommand with a HARD cmdlet allowlist.

Shared mailboxes and mailbox permissions are not exposed by Microsoft Graph; the supported
programmatic channel is the EXO admin REST API (what Exchange Online PowerShell v3 uses).
This client POSTs `{"CmdletInput": {"CmdletName": ..., "Parameters": {...}}}` to
`https://outlook.office365.com/adminapi/beta/<tenant-guid>/InvokeCommand`.

Security model (SOP: exchange-online.md):
  - CmdletName must EXACTLY match the allowlist below — extend deliberately, never
    AI-improvised (same rule as scopes.READ_SCOPES).
  - Broad cmdlets (Set-Mailbox) are ALSO parameter-allowlisted (PARAM_ALLOWLIST, D-55).
  - FORCED_PARAMS pins safety-critical switches: New-Mailbox is always Shared:true;
    Enable-/Disable-Mailbox are always Archive:true (archive toggle only — they can
    never mailbox-disable an account).
  - Destructive cmdlets (Remove-*) live OUTSIDE the allowlist — D-54, invoke_destructive().
  - Parameters travel as JSON data, never PowerShell text → no script-injection surface.
  - Built per (exo, tenant) and fail-closed when that client's Exchange isn't signed in.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ._http import HttpError, http_json

# cmdlet → kind. The KIND is documentation here; enforcement of WHETHER a write may run
# lives in dispatch() (CATEGORY=write ⇒ allow_write + approval). This list bounds WHAT
# can ever be invoked at all.
ALLOWED_CMDLETS: dict[str, str] = {
    "Get-Mailbox": "read",
    "Get-MailboxPermission": "read",
    "Get-RecipientPermission": "read",
    "Get-AcceptedDomain": "read",
    "Get-MailboxStatistics": "read",     # mailbox + archive sizes (D-55)
    "Get-DistributionGroup": "read",
    "Get-DistributionGroupMember": "read",
    "Get-UnifiedGroup": "read",          # Microsoft 365 groups
    "Get-UnifiedGroupLinks": "read",
    "Get-RetentionPolicy": "read",
    "Get-RetentionPolicyTag": "read",
    "Get-MailContact": "read",
    "Get-Recipient": "read",             # D-105 — "which groups is this user in?" (authoritative DLs)
    "Get-MailboxFolderPermission": "read",
    "Get-MailboxJunkEmailConfiguration": "read",
    "Get-TransportRule": "read",
    "Get-InboundConnector": "read",
    "New-Mailbox": "write",              # forced Shared:true below
    "Add-MailboxPermission": "write",
    "Add-RecipientPermission": "write",
    "Set-Mailbox": "write",              # bounded further by PARAM_ALLOWLIST (D-55)
    "Add-DistributionGroupMember": "write",
    "Add-UnifiedGroupLinks": "write",
    "Enable-Mailbox": "write",           # forced Archive:true — archive toggle ONLY
    "Disable-Mailbox": "write",          # forced Archive:true — can never mailbox-disable
    "Start-ManagedFolderAssistant": "write",  # D-113 — force retention/archive processing now
                                              # (triggers the scheduled assistant; no data created)
    "New-RetentionPolicyTag": "write",   # retention management (D-55) — param-allowlisted
    "New-RetentionPolicy": "write",
    "Set-RetentionPolicy": "write",
    "New-DistributionGroup": "write",    # D-56 — param-allowlisted
    "New-MailContact": "write",          # D-56 — param-allowlisted
    "Add-MailboxFolderPermission": "write",   # D-58 — calendar/contacts delegation
    "Set-MailboxFolderPermission": "write",
    "Remove-MailboxPermission": "write",      # D-63 — revoking a permission is a WRITE:
    "Remove-RecipientPermission": "write",    # no data is destroyed and re-granting undoes it
    "Remove-MailboxFolderPermission": "write",  # (destructive = data loss, i.e. Remove-Mailbox)
    "Remove-DistributionGroupMember": "write",  # D-65 — remove counterparts (object/config
    "Remove-UnifiedGroupLinks": "write",        # removal, re-creatable → write, not destructive)
    "Remove-DistributionGroup": "write",
    "Remove-MailContact": "write",
    "Remove-RetentionPolicyTag": "write",
    "Remove-RetentionPolicy": "write",
    "Remove-TransportRule": "write",
    "Remove-InboundConnector": "write",
    "Set-MailboxJunkEmailConfiguration": "write",
    "New-TransportRule": "write",        # D-58 — bounded to the two known rule shapes
    "New-InboundConnector": "write",
}

# Security & Compliance cmdlets (D-58) ride a SEPARATE endpoint + token audience
# (ps.compliance.protection.outlook.com) — reachable only via invoke_compliance().
COMPLIANCE_CMDLETS: dict[str, str] = {
    "Get-ProtectionAlert": "read",
    "New-ProtectionAlert": "write",
    "Remove-ProtectionAlert": "write",   # D-65
    # Content Search / Purview eDiscovery (D-115 preview; D-116 adds export).
    # New-ComplianceSearchAction is param-allowlisted to {SearchName, Preview, Export} — so its
    # -Purge (DESTRUCTIVE: deletes matching mail) stays unreachable here by design.
    "New-ComplianceSearch": "write",
    "Start-ComplianceSearch": "write",
    "Get-ComplianceSearch": "read",
    "New-ComplianceSearchAction": "write",
    "Get-ComplianceSearchAction": "read",
}
COMPLIANCE_BASE = "https://ps.compliance.protection.outlook.com/adminapi/beta"

# Parameters FORCED onto a cmdlet (D-55, generalizes the old New-Mailbox special case).
# Enable-/Disable-Mailbox are pinned to ARCHIVE operations only — mailbox-disabling/-enabling
# an account is destructive and stays off this connector. Enable-Mailbox is special-cased in
# invoke(): Archive and AutoExpandingArchive are different Exchange parameter sets, so when
# AutoExpandingArchive:true is the operation we must NOT also force Archive:true.
FORCED_PARAMS: dict[str, dict] = {
    "New-Mailbox": {"Shared": True},     # this connector creates SHARED mailboxes only
    "Disable-Mailbox": {"Archive": True},
}
_ENABLE_MAILBOX_ARCHIVE_SWITCHES = ("Archive", "AutoExpandingArchive")

# Set-Mailbox is broad; bound WHICH parameters may travel (D-55) so even a promoted AI
# draft can't reach litigation hold, audit bypass, etc. Extend deliberately, by hand.
PARAM_ALLOWLIST: dict[str, frozenset] = {
    "Set-Mailbox": frozenset({
        "Identity", "Confirm",
        "HiddenFromAddressListsEnabled",                  # GAL visibility
        "WindowsEmailAddress", "MicrosoftOnlineServicesID",  # primary SMTP + sign-in UPN
        "EmailAddresses",                                 # aliases (hashtable Add/Remove)
        "Type",                                           # shared ⇄ regular
        "MaxSendSize", "MaxReceiveSize",                  # quotas
        "ForwardingSmtpAddress", "DeliverToMailboxAndForward",
        "RetentionPolicy",
        "GrantSendOnBehalfTo",                            # delegate: send on behalf
        "IsExchangeCloudManaged",                         # let EXO master mailbox settings for an
                                                          # AD-synced user (D-91) — reversible flag
    }),
    "New-RetentionPolicyTag": frozenset({
        "Name", "Type", "RetentionAction", "AgeLimitForRetention", "RetentionEnabled",
        "Comment", "Confirm",
    }),
    "New-RetentionPolicy": frozenset({"Name", "RetentionPolicyTagLinks", "Confirm"}),
    "Set-RetentionPolicy": frozenset({"Identity", "RetentionPolicyTagLinks", "Confirm"}),
    "Enable-Mailbox": frozenset({"Identity", "Confirm", "Archive", "AutoExpandingArchive"}),
    "Disable-Mailbox": frozenset({"Identity", "Confirm", "Archive"}),
    "Start-ManagedFolderAssistant": frozenset({"Identity"}),  # D-113 — target one primary mailbox GUID
    "New-DistributionGroup": frozenset({
        "Name", "DisplayName", "Alias", "PrimarySmtpAddress", "Type", "Members", "Confirm",
    }),
    "New-MailContact": frozenset({
        "Name", "DisplayName", "ExternalEmailAddress", "FirstName", "LastName", "Confirm",
    }),
    "Add-MailboxFolderPermission": frozenset({"Identity", "User", "AccessRights", "Confirm"}),
    "Set-MailboxFolderPermission": frozenset({"Identity", "User", "AccessRights", "Confirm"}),
    "Remove-MailboxPermission": frozenset({"Identity", "User", "AccessRights", "Confirm"}),
    "Remove-RecipientPermission": frozenset({"Identity", "Trustee", "AccessRights", "Confirm"}),
    "Remove-MailboxFolderPermission": frozenset({"Identity", "User", "Confirm"}),
    "Set-MailboxJunkEmailConfiguration": frozenset({"Identity", "Enabled", "Confirm"}),
    "New-TransportRule": frozenset({
        "Name", "Comments", "Enabled", "Confirm",
        "FromScope", "SentToScope", "MessageTypeMatches", "RejectMessageReasonText",
        "SenderIPRanges", "SetSCL",
    }),
    "New-InboundConnector": frozenset({
        "Name", "Comment", "ConnectorType", "SenderDomains", "RestrictDomainsToIPAddresses",
        "RequireTls", "SenderIPAddresses", "Enabled", "Confirm",
    }),
    "New-ProtectionAlert": frozenset({
        "Name", "Category", "ThreatType", "Operation", "Severity", "NotifyUser",
        "AggregationType", "Description",
    }),
    # Content Search (D-115 preview, D-116 export). Deliberately NARROW: New-ComplianceSearchAction
    # allows only SearchName + Preview + Export — never -Purge, so the destructive delete-mail action
    # stays unreachable (see COMPLIANCE_CMDLETS). Get- gains IncludeCredential to read the export's
    # SAS URL (server-side only — never returned to a caller).
    "New-ComplianceSearch": frozenset({
        "Name", "ExchangeLocation", "ExchangeLocationExclusion", "ContentMatchQuery",
        "Description", "AllowNotFoundExchangeLocationsEnabled",
    }),
    "Start-ComplianceSearch": frozenset({"Identity"}),
    "Get-ComplianceSearch": frozenset({"Identity", "ResultSize"}),
    "New-ComplianceSearchAction": frozenset({"SearchName", "Preview", "Export"}),
    "Get-ComplianceSearchAction": frozenset({"Identity", "Details", "ResultSize", "IncludeCredential"}),
    "Remove-DistributionGroupMember": frozenset({
        "Identity", "Member", "Confirm", "BypassSecurityGroupManagerCheck"}),
    "Remove-UnifiedGroupLinks": frozenset({"Identity", "LinkType", "Links", "Confirm"}),
    "Remove-DistributionGroup": frozenset({"Identity", "Confirm"}),
    "Remove-MailContact": frozenset({"Identity", "Confirm"}),
    "Remove-RetentionPolicyTag": frozenset({"Identity", "Confirm"}),
    "Remove-RetentionPolicy": frozenset({"Identity", "Confirm"}),
    "Remove-TransportRule": frozenset({"Identity", "Confirm"}),
    "Remove-InboundConnector": frozenset({"Identity", "Confirm"}),
    "Remove-ProtectionAlert": frozenset({"Identity", "Confirm"}),
}


def hashtable(d: dict) -> dict:
    """A PowerShell hashtable parameter (e.g. EmailAddresses @{Add=...}) on the EXO REST wire."""
    return {"@odata.type": "#Exchange.GenericHashTable", **d}

# Destructive cmdlets live OUTSIDE the normal allowlist (D-54): invoke() refuses them, so no
# read/write tool — including anything AI-drafted — can reach deletion. Only a hand-written
# CATEGORY=destructive skill calls invoke_destructive(), and dispatch's destructive floor means
# every such run requires a fresh owner approval that can never be toggled off.
DESTRUCTIVE_CMDLETS: frozenset = frozenset({"Remove-Mailbox"})


class EXOClient:
    def __init__(self, token_source: Callable[[], str], tenant_guid: str, anchor_upn: str = "",
                 *, transport: Callable = http_json,
                 base: str = "https://outlook.office365.com/adminapi/beta",
                 compliance_token: Optional[Callable[[], str]] = None,
                 compliance_base: str = COMPLIANCE_BASE,
                 granted_cmdlets: Optional[dict] = None,
                 granted_params: Optional[dict] = None) -> None:
        self._token = token_source
        self._tid = (tenant_guid or "").strip()
        self._anchor = (anchor_upn or "").strip()
        self._t = transport
        self.base = base.rstrip("/")
        self._ctoken = compliance_token
        self.compliance_base = compliance_base.rstrip("/")
        # owner-approved self-extension grants (D-64) — NEW read/write cmdlets only; never
        # destructive (filtered here too, belt-and-suspenders).
        self._granted = {k: v for k, v in (granted_cmdlets or {}).items()
                         if k not in DESTRUCTIVE_CMDLETS and k not in ALLOWED_CMDLETS}
        self._granted_params = dict(granted_params or {})

    def invoke(self, cmdlet: str, parameters: Optional[dict] = None) -> Any:
        """Run ONE allow-listed (read/write) cmdlet. Refuses anything else — including every
        destructive cmdlet — before any HTTP happens."""
        cmdlet = (cmdlet or "").strip()
        if cmdlet not in ALLOWED_CMDLETS and cmdlet not in self._granted:
            return {"error": f"cmdlet '{cmdlet}' is not in the EXO allowlist "
                             f"(allowed: {', '.join(sorted(ALLOWED_CMDLETS))})"
                             + (f"; owner-granted: {', '.join(sorted(self._granted))}"
                                if self._granted else "")}
        params = dict(parameters or {})
        # granted cmdlets are ALWAYS param-allowlisted to exactly what the owner approved
        allowed_params = PARAM_ALLOWLIST.get(cmdlet)
        if allowed_params is None and cmdlet in self._granted:
            allowed_params = self._granted_params.get(cmdlet, frozenset())
        if allowed_params is not None:
            unknown = sorted(set(params) - allowed_params)
            if unknown:
                return {"error": f"{cmdlet} parameter(s) not in the allowlist: "
                                 f"{', '.join(unknown)} (allowed: "
                                 f"{', '.join(sorted(allowed_params))})"}
        params.update(FORCED_PARAMS.get(cmdlet, {}))
        if cmdlet == "Enable-Mailbox" and not any(
                params.get(s) is True for s in _ENABLE_MAILBOX_ARCHIVE_SWITCHES):
            params["Archive"] = True      # archive ops only — never plain mailbox-enable
        return self._post_cmdlet(cmdlet, params)

    def invoke_destructive(self, cmdlet: str, parameters: Optional[dict] = None) -> Any:
        """Run ONE destructive cmdlet (D-54). Callable ONLY from a hand-written
        CATEGORY=destructive skill — dispatch's floor forces a fresh owner approval per run."""
        cmdlet = (cmdlet or "").strip()
        if cmdlet not in DESTRUCTIVE_CMDLETS:
            return {"error": f"cmdlet '{cmdlet}' is not in the EXO destructive allowlist "
                             f"(allowed: {', '.join(sorted(DESTRUCTIVE_CMDLETS))})"}
        return self._post_cmdlet(cmdlet, dict(parameters or {}))

    def invoke_compliance(self, cmdlet: str, parameters: Optional[dict] = None) -> Any:
        """Run ONE Security & Compliance cmdlet (D-58) — separate endpoint, separate token
        audience (minted from the same Exchange sign-in), separate tiny allowlist."""
        cmdlet = (cmdlet or "").strip()
        if cmdlet not in COMPLIANCE_CMDLETS:
            return {"error": f"cmdlet '{cmdlet}' is not in the Security & Compliance "
                             f"allowlist (allowed: {', '.join(sorted(COMPLIANCE_CMDLETS))})"}
        params = dict(parameters or {})
        allowed_params = PARAM_ALLOWLIST.get(cmdlet)
        if allowed_params is not None:
            unknown = sorted(set(params) - allowed_params)
            if unknown:
                return {"error": f"{cmdlet} parameter(s) not in the allowlist: "
                                 f"{', '.join(unknown)}"}
        if self._ctoken is None:
            return {"error": "no Security & Compliance access for this client — re-connect "
                             "its Exchange sign-in on the M365 card"}
        return self._post_cmdlet(cmdlet, params, base=self.compliance_base,
                                 token=self._ctoken)

    def _post_cmdlet(self, cmdlet: str, params: dict, *, base: Optional[str] = None,
                     token: Optional[Callable[[], str]] = None) -> Any:
        if not self._tid:
            return {"error": "no tenant GUID for the Exchange connection — re-sign-in needed"}
        headers = {"Authorization": f"Bearer {(token or self._token)()}",
                   "X-ResponseFormat": "json",
                   "Accept": "application/json"}
        if self._anchor:
            headers["X-AnchorMailbox"] = f"UPN:{self._anchor}"
        try:
            _s, data = self._t("POST", f"{base or self.base}/{self._tid}/InvokeCommand",
                               headers=headers,
                               json_body={"CmdletInput": {"CmdletName": cmdlet,
                                                          "Parameters": params}})
        except HttpError as e:
            hint = (" (admin role / re-consent needed?)" if e.status in (401, 403)
                    else "")
            return {"error": f"EXO HTTP {e.status}{hint}: {e.body[:300]}"}
        if isinstance(data, dict) and "value" in data:
            return data["value"]
        return data if data is not None else {"ok": True}

    def probe(self) -> dict[str, Any]:
        r = self.invoke("Get-AcceptedDomain")
        if isinstance(r, dict) and r.get("error"):
            return {"ok": False, "detail": str(r["error"])[:200]}
        names = [d.get("DomainName") or d.get("Name") for d in r if isinstance(d, dict)] \
            if isinstance(r, list) else []
        return {"ok": True,
                "detail": f"Exchange ok; domains: {', '.join(str(n) for n in names[:3] if n)}"
                          or "Exchange ok"}


def build_exo(cfg, tenant: str) -> "EXOClient":
    """Build an Exchange client for ONE managed client. Fail-closed if that client's Exchange
    isn't signed in (or the session is '*' — EXO is per-client like Graph, D-33/D-41)."""
    from ..core import m365_auth
    from ..core.credentials import MissingCredential
    if (tenant or "").strip() in ("", "*"):
        raise MissingCredential("Exchange Online is per-client — pick a specific client first")
    if not m365_auth.is_connected(cfg, tenant, service="exo"):
        raise MissingCredential(
            f"Exchange Online: client '{tenant}' is not signed in — connect Exchange on the "
            f"M365 card (separate from the Graph sign-in)")
    # tenant GUID + admin UPN come from the non-secret sidecar (no vault decrypt needed here)
    tid = str(m365_auth._read_side(cfg, tenant, "exo").get("tenant_id") or "")
    upn = m365_auth.admin_upn(cfg, tenant, "exo")
    from ..core import connector_grants
    gc, gp = connector_grants.grants_for("exo")
    return EXOClient(m365_auth.token_source(cfg, tenant, service="exo"), tid, upn,
                     compliance_token=m365_auth.compliance_token_source(cfg, tenant),
                     granted_cmdlets=gc, granted_params=gp)
