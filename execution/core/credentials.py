"""Credential layer (Invariant I-2) — ported & generalized from Kaseya Link.

`require(integration)` is the ONLY sanctioned path to a vendor client's credentials.
It fails closed: if ANY required key for the integration is missing, it raises rather
than returning a partial/None-bearing credential set (Behavioral Rule #8).

The admin/status surface uses `status()` which reveals only sha256[:7] fingerprints,
never raw secrets.

This module knows nothing about HTTP. The actual client classes live in
execution/clients/ and are constructed from the dict require() returns. That keeps the
core dependency-free and unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .config import Config, fingerprint, get_config


class MissingCredential(RuntimeError):
    """A required credential key was absent — no client may be built."""


@dataclass(frozen=True)
class CredentialSpec:
    integration: str                 # "kaseya" | "cylance" | "huntress" | ...
    required: tuple[str, ...]        # env keys that MUST be present
    optional: tuple[str, ...] = ()   # env keys used if present (e.g. region, static token)
    label: str = ""                  # human label for the UI
    group: str = "vendor"            # "vendor" (MSP API) | "llm" (AI model provider)

    @property
    def display(self) -> str:
        return self.label or self.integration.title()


# Registry of GREEN integrations (Phase L). Add a CredentialSpec here to make an
# integration known to the status page; clients/ provides the matching client class.
SPECS: dict[str, CredentialSpec] = {
    "kaseya": CredentialSpec(
        "kaseya",
        required=("KASEYA_URL",),
        optional=("KASEYA_USER", "KASEYA_PASS", "KASEYA_TOKEN"),
        label="Kaseya VSA",
    ),
    "cylance": CredentialSpec(
        "cylance",
        required=("CYLANCE_TENANT_ID", "CYLANCE_APP_ID", "CYLANCE_APP_SECRET"),
        optional=("CYLANCE_REGION",),
        label="Cylance",
    ),
    "huntress": CredentialSpec(
        "huntress",
        required=("HUNTRESS_API_KEY", "HUNTRESS_API_SECRET"),
        label="Huntress",
    ),
    "freshdesk": CredentialSpec(
        "freshdesk",
        required=("FRESHDESK_DOMAIN", "FRESHDESK_API_KEY"),
        label="Freshdesk",
    ),
    "unifi": CredentialSpec(
        "unifi",
        required=("UNIFI_URL", "UNIFI_API_KEY"),
        optional=("UNIFI_VERIFY_TLS",),          # default off — LAN console self-signed cert
        label="UniFi Network (local)",
    ),
    "proofpoint": CredentialSpec(
        "proofpoint",
        required=("PROOFPOINT_REGION", "PROOFPOINT_USER", "PROOFPOINT_PASSWORD"),
        label="Proofpoint Essentials",
    ),
    # Communication channels (D-28/D-29) — alert email + the MS Teams bot.
    "email": CredentialSpec(
        "email",
        required=("EMAIL_FROM",),
        optional=("EMAIL_MODE", "EMAIL_API_KEY", "EMAIL_API_URL",
                  "EMAIL_SMTP_HOST", "EMAIL_SMTP_PORT", "EMAIL_SMTP_USER", "EMAIL_SMTP_PASS",
                  "EMAIL_SMTP_SECURITY", "EMAIL_DEFAULT_TO", "EMAIL_ALLOWED_RECIPIENTS"),
        label="Email (alerts)", group="comms",
    ),
    # Secret is OPTIONAL: the app can instead authenticate with the locally generated
    # certificate (core/teams_cert.py) — the client fails closed when neither exists.
    # Microsoft 365 / Graph (D-32/D-33/D-34) — each managed client signs in SEPARATELY via device code
    # using Microsoft's built-in sign-in app (no app registration required); tokens are cached
    # per-client. M365_CLIENT_ID is an OPTIONAL override for owners who prefer their own app.
    "m365": CredentialSpec(
        "m365",
        required=(),
        optional=("M365_CLIENT_ID", "M365_TENANT", "M365_SCOPES"),
        label="Microsoft 365 (Graph + Exchange)", group="vendor",
    ),
    # Google Workspace admin (D-118) — per-client OAuth (authorization-code flow). Each client
    # registers their OWN OAuth app; its client_id/secret live PER CLIENT in the CredVault
    # (gws_app), entered on that client's card — NOT here. The only global setting is the redirect
    # URI (our dashboard callback, which every client's app registers). GWS_SCOPES optionally
    # overrides the requested scopes. required is empty (like m365).
    "gws": CredentialSpec(
        "gws",
        required=(),
        optional=("GWS_REDIRECT_URI", "GWS_SCOPES"),
        label="Google Workspace (Admin)", group="vendor",
    ),
    "msteams": CredentialSpec(
        "msteams",
        required=("TEAMS_CLIENT_ID", "TEAMS_TENANT_ID"),
        optional=("TEAMS_CLIENT_SECRET", "TEAMS_ALLOWED_USERS", "TEAMS_ALLOW_ALL_USERS",
                  "TEAMS_BIND_TENANT", "TEAMS_PROFILE", "TEAMS_ALLOW_CLOUD",
                  "TEAMS_HOME_CONVERSATION", "TEAMS_SERVICE_URL"),
        label="Microsoft Teams", group="comms",
    ),
    # AI model providers — secure key entry reuses the same credential form.
    "anthropic": CredentialSpec(
        "anthropic", required=("ANTHROPIC_API_KEY",), label="Claude (Anthropic)", group="llm"),
    "openai": CredentialSpec(
        "openai", required=("OPENAI_API_KEY",), label="OpenAI", group="llm"),
    # ChatGPT-plan OAuth (Codex) — subscription auth, no API key. The refresh token is the
    # durable credential; the access token is a short-lived cache that codex_auth rotates
    # in place (which is why these live in the SecretStore, not env). SOP: openai-codex.md.
    "openai_codex": CredentialSpec(
        "openai_codex",
        required=("OPENAI_CODEX_REFRESH_TOKEN",),
        optional=("OPENAI_CODEX_ACCESS_TOKEN",),
        label="OpenAI — ChatGPT plan (Codex)", group="llm"),
}


# Per-key UI metadata for the credential form: friendly label, plain-language help,
# whether the value is a secret (password input + fingerprint-only) and an example
# placeholder. Keys absent here render as before (key name, password input).
# `hidden` keys are managed by a dedicated UI (e.g. the Teams allowlist panel) and
# are not shown in the raw form.
FIELD_INFO: dict[str, dict] = {
    # ── Kaseya VSA ──
    "KASEYA_URL": {"label": "VSA server URL", "secret": False,
                   "placeholder": "https://vsa.yourcompany.com",
                   "help": "Your VSA web address — scheme + host only, no path."},
    "KASEYA_USER": {"label": "API username", "secret": False,
                    "help": "A VSA user with API access (System → Users). Not needed if you use a static token."},
    "KASEYA_PASS": {"label": "API password",
                    "help": "That user's password. Used to fetch a short-lived bearer token."},
    "KASEYA_TOKEN": {"label": "Static token (alternative)",
                     "help": "A personal access token — fill this instead of username + password if you prefer."},
    # ── Cylance ──
    "CYLANCE_TENANT_ID": {"label": "Tenant ID", "secret": False,
                          "help": "Cylance console → Settings → Integrations: the Tenant APP's tenant id (GUID)."},
    "CYLANCE_APP_ID": {"label": "Application ID", "secret": False,
                       "help": "From the same Integrations page — the custom application's id."},
    "CYLANCE_APP_SECRET": {"label": "Application secret",
                           "help": "The application's secret — shown once when the integration app is created."},
    "CYLANCE_REGION": {"label": "Region", "secret": False, "placeholder": "NA",
                       "help": "Console region: NA (default), US-GOV, EU, AU, SAE, APNE1, APSE2."},
    # ── Huntress ──
    "HUNTRESS_API_KEY": {"label": "API key", "secret": False,
                         "help": "Huntress portal → top-right profile → API Credentials → public key."},
    "HUNTRESS_API_SECRET": {"label": "API secret",
                            "help": "The matching private key — shown once when the credential is generated."},
    # ── LLM providers ──
    "ANTHROPIC_API_KEY": {"label": "API key",
                          "help": "console.anthropic.com → API Keys. Enables Claude models in the chat picker."},
    "OPENAI_API_KEY": {"label": "API key",
                       "help": "platform.openai.com → API Keys. Or skip this and use Sign in with ChatGPT below."},
    # ── Email (D-28) ──
    "EMAIL_FROM": {"label": "From address", "secret": False,
                   "placeholder": "MSP AI <alerts@yourdomain.com>",
                   "help": "The sender on outgoing mail. Must be an address/domain your relay is allowed "
                           "to send as (smtp2go: a verified sender domain)."},
    "EMAIL_MODE": {"label": "Transport", "secret": False, "placeholder": "auto",
                   "help": "api or smtp. Leave blank to auto-detect: API key set → api, otherwise smtp."},
    "EMAIL_API_KEY": {"label": "API key (smtp2go)",
                      "help": "smtp2go → Sending → API Keys. The simplest setup — with this set you can "
                              "leave every SMTP field empty."},
    "EMAIL_API_URL": {"label": "API base URL", "secret": False,
                      "placeholder": "https://api.smtp2go.com/v3",
                      "help": "Leave blank for smtp2go. Only set for another smtp2go-compatible relay."},
    "EMAIL_SMTP_HOST": {"label": "SMTP server", "secret": False,
                        "placeholder": "mail.smtp2go.com",
                        "help": "SMTP relay hostname (smtp2go SMTP, M365, your own postfix…). "
                                "Only needed when not using the API key."},
    "EMAIL_SMTP_PORT": {"label": "SMTP port", "secret": False, "placeholder": "587",
                        "help": "587 = STARTTLS (default) · 465 = SSL · 25 = plain. Must match Security below."},
    "EMAIL_SMTP_USER": {"label": "SMTP username", "secret": False,
                        "help": "Relay login. Leave empty for relays that authenticate by IP."},
    "EMAIL_SMTP_PASS": {"label": "SMTP password",
                        "help": "The relay login's password (smtp2go: an SMTP user's password, not the API key)."},
    "EMAIL_SMTP_SECURITY": {"label": "Security", "secret": False, "placeholder": "starttls",
                            "help": "starttls (default), ssl, or none — pick the one matching the port."},
    "EMAIL_DEFAULT_TO": {"label": "Default recipient", "secret": False,
                         "placeholder": "team@yourdomain.com",
                         "help": "Where alerts go when no recipient is specified. Always allowed to receive."},
    "EMAIL_ALLOWED_RECIPIENTS": {"hidden": True},   # managed by the recipients panel on the card
    # ── Microsoft 365 / Graph (D-32) ──
    "M365_CLIENT_ID": {"label": "Application (client) ID — optional", "secret": False,
                       "placeholder": "leave blank to use Microsoft's built-in sign-in app",
                       "help": "Leave BLANK (recommended): each client signs in through Microsoft's "
                               "own app — no Azure registration needed. Only set this if you've "
                               "registered your own public-client app and want to use it instead."},
    "M365_TENANT": {"label": "Tenant", "secret": False, "placeholder": "organizations",
                    "help": "Leave as 'organizations' for any work/school tenant, or paste a specific "
                            "tenant GUID to lock sign-in to one client."},
    "M365_SCOPES": {"label": "Scopes", "secret": False,
                    "placeholder": "offline_access openid profile User.Read.All",
                    "help": "Delegated Graph permissions to request at sign-in. Keep offline_access "
                            "(needed to stay signed in). Full set for every current capability: "
                            "offline_access openid profile User.Read.All User.ReadWrite.All "
                            "User-PasswordProfile.ReadWrite.All Organization.Read.All "
                            "Group.ReadWrite.All UserAuthenticationMethod.ReadWrite.All "
                            "Policy.Read.All Policy.ReadWrite.AuthenticationMethod Sites.Read.All "
                            "DeviceManagementServiceConfig.ReadWrite.All — re-sign-in each client "
                            "after changing."},
    # M365 refresh/access tokens are stored PER CLIENT (vault/clients/<t>/m365.json), not as
    # global keys — they never appear in the credential form (D-33).
    # ── Microsoft Teams (D-29) ──
    "TEAMS_CLIENT_ID": {"label": "Application (client) ID", "secret": False,
                        "placeholder": "00000000-0000-0000-0000-000000000000",
                        "help": "Entra → App registrations → your app → Overview. The Teams CLI prints it "
                                "as CLIENT_ID after `teams app create`."},
    "TEAMS_TENANT_ID": {"label": "Directory (tenant) ID", "secret": False,
                        "placeholder": "00000000-0000-0000-0000-000000000000",
                        "help": "Entra ID → Overview (your M365 tenant's GUID). Also printed by the Teams CLI."},
    "TEAMS_CLIENT_SECRET": {"label": "Client secret",
                            "help": "App registration → Certificates & secrets → New client secret (copy "
                                    "the Value, not the ID). OPTIONAL — generate the app certificate below "
                                    "instead and leave this empty."},
    "TEAMS_BIND_TENANT": {"label": "Bound managed client", "secret": False, "placeholder": "*",
                          "help": "Which managed client Teams chats can see. * (default) = the all-clients "
                                  "read view. Fixed server-side — Teams users cannot change it."},
    "TEAMS_PROFILE": {"label": "Agent profile", "secret": False, "placeholder": "default",
                      "help": "Which specialist answers in Teams. Blank = the AtlasOps manager."},
    "TEAMS_ALLOW_CLOUD": {"label": "Allow cloud models", "secret": False, "placeholder": "0",
                          "help": "1 lets Teams turns use cloud models (Claude/OpenAI). Default 0 = "
                                  "local model only, per the local-first rule."},
    "TEAMS_HOME_CONVERSATION": {"label": "Home conversation ID", "secret": False,
                                "help": "Where the teams_notify alert tool posts. DM the bot once, then "
                                        "copy the conversation id from that chat's entry in the Audit tab."},
    "TEAMS_SERVICE_URL": {"label": "Service URL override", "secret": False,
                          "help": "Only for gov/regional clouds (must be a known Bot Framework host). "
                                  "Leave blank for commercial Teams."},
    "TEAMS_ALLOWED_USERS": {"hidden": True},      # managed by the allowlist panel below
    "TEAMS_ALLOW_ALL_USERS": {"hidden": True},
}


def spec_for(integration: str) -> Optional[CredentialSpec]:
    """Resolve an integration's CredentialSpec: built-in SPECS first, then the owner's
    custom integrations (D-27) — which surface with group='custom' and the owner's labels."""
    spec = SPECS.get(integration)
    if spec is not None:
        return spec
    try:
        from .custom_integrations import get_store
        ci = get_store().get(integration)
    except Exception:        # store unreadable → behave as unknown (fail closed)
        return None
    if ci is None:
        return None
    return CredentialSpec(ci.id, required=ci.required_keys, optional=ci.optional_keys,
                          label=ci.label, group="custom")


def require(integration: str, cfg: Optional[Config] = None) -> dict[str, str]:
    """Return all credential values for `integration`, or raise if any required key is missing."""
    cfg = cfg or get_config()
    spec = spec_for(integration)
    if spec is None:
        raise MissingCredential(f"unknown integration '{integration}'")
    missing = [k for k in spec.required if not cfg.present(k)]
    if missing:
        raise MissingCredential(
            f"{spec.display}: missing required credential(s): {', '.join(missing)}"
        )
    creds: dict[str, str] = {}
    for k in (*spec.required, *spec.optional):
        v = cfg.get(k)
        if v:
            creds[k] = v
    return creds


def allowed_keys(integration: str) -> set[str]:
    spec = spec_for(integration)
    if spec is None:
        raise MissingCredential(f"unknown integration '{integration}'")
    return set(spec.required) | set(spec.optional)


def set_integration(integration: str, values: dict[str, str], cfg: Optional[Config] = None) -> "CredStatus":
    """Securely store credentials for one integration (UI entry point).

    Only keys belonging to this integration's CredentialSpec may be written (allowlist),
    so this cannot inject arbitrary config. Empty value clears a key. Requires a SecretStore
    on the config (the running app has one). Returns the new fingerprint-only status.
    """
    cfg = cfg or get_config()
    spec = spec_for(integration)
    if spec is None:
        raise MissingCredential(f"unknown integration '{integration}'")
    if getattr(cfg, "secrets", None) is None:
        raise MissingCredential("no secret store configured; cannot persist credentials")
    allow = allowed_keys(integration)
    unknown = set(values) - allow
    if unknown:
        raise MissingCredential(f"keys not valid for {spec.display}: {', '.join(sorted(unknown))}")
    cfg.secrets.set_many({k: v for k, v in values.items()}, allowed_keys=allow)
    return next(s for s in status(cfg) if s.integration == integration)


def is_configured(integration: str, cfg: Optional[Config] = None) -> bool:
    cfg = cfg or get_config()
    spec = spec_for(integration)
    if not spec:
        return False
    return all(cfg.present(k) for k in spec.required)


@dataclass
class CredStatus:
    integration: str
    label: str
    configured: bool
    group: str = "vendor"
    fingerprints: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def all_specs() -> dict[str, CredentialSpec]:
    """Built-in SPECS plus the owner's custom integrations (D-27)."""
    out = dict(SPECS)
    try:
        from .custom_integrations import get_store
        for ci in get_store().all():
            if ci.id not in out:
                out[ci.id] = CredentialSpec(ci.id, required=ci.required_keys,
                                            optional=ci.optional_keys, label=ci.label,
                                            group="custom")
    except Exception:
        pass                              # store unreadable → show built-ins only
    return out


def status(cfg: Optional[Config] = None) -> list[CredStatus]:
    """Fingerprint-only status of every known integration — safe to show in the UI."""
    cfg = cfg or get_config()
    out: list[CredStatus] = []
    for name, spec in all_specs().items():
        fps = {k: fingerprint(cfg.get(k)) for k in (*spec.required, *spec.optional) if cfg.present(k)}
        missing = [k for k in spec.required if not cfg.present(k)]
        out.append(
            CredStatus(
                integration=name,
                label=spec.display,
                configured=not missing,
                group=spec.group,
                fingerprints=fps,
                missing=missing,
            )
        )
    return out
