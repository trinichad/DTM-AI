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

    @property
    def display(self) -> str:
        return self.label or self.integration.title()


# Registry of GREEN integrations (Phase L). Add a CredentialSpec here to make an
# integration known to the status page; clients/ provides the matching client class.
SPECS: dict[str, CredentialSpec] = {
    "kaseya": CredentialSpec(
        "kaseya",
        required=("KASEYA_BASE_URL",),
        optional=("KASEYA_USER", "KASEYA_PASSWORD", "KASEYA_TOKEN"),
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
}


def require(integration: str, cfg: Optional[Config] = None) -> dict[str, str]:
    """Return all credential values for `integration`, or raise if any required key is missing."""
    cfg = cfg or get_config()
    spec = SPECS.get(integration)
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


def is_configured(integration: str, cfg: Optional[Config] = None) -> bool:
    cfg = cfg or get_config()
    spec = SPECS.get(integration)
    if not spec:
        return False
    return all(cfg.present(k) for k in spec.required)


@dataclass
class CredStatus:
    integration: str
    label: str
    configured: bool
    fingerprints: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def status(cfg: Optional[Config] = None) -> list[CredStatus]:
    """Fingerprint-only status of every known integration — safe to show in the UI."""
    cfg = cfg or get_config()
    out: list[CredStatus] = []
    for name, spec in SPECS.items():
        fps = {k: fingerprint(cfg.get(k)) for k in (*spec.required, *spec.optional) if cfg.present(k)}
        missing = [k for k in spec.required if not cfg.present(k)]
        out.append(
            CredStatus(
                integration=name,
                label=spec.display,
                configured=not missing,
                fingerprints=fps,
                missing=missing,
            )
        )
    return out
