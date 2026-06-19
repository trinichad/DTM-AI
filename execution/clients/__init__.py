"""Vendor API clients (the credentialed edge of the T-layer).

Each client is read-only by default and constructed ONLY via credentials.require()
(Invariant I-2). Clients take an injectable `transport` so skills + tests exercise their
pagination/slimming logic with no network. The ClientFactory is wired into ToolContext so
a tool gets a client for its bound tenant via `ctx.client("kaseya")`.

GREEN integrations (Phase L): kaseya, cylance, huntress.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..core.config import Config, get_config
from ..core import credentials
from .cylance import CylanceClient
from .freshdesk import FreshdeskClient
from .huntress import HuntressClient
from .kaseya import KaseyaClient
from .proofpoint import ProofpointClient
from .unifi import UnifiClient

_BUILDERS: dict[str, Callable[[dict], Any]] = {
    "kaseya": lambda env: KaseyaClient(
        env["KASEYA_URL"], env.get("KASEYA_USER"), env.get("KASEYA_PASS"),
        token=env.get("KASEYA_TOKEN"),
    ),
    "cylance": lambda env: CylanceClient(
        env.get("CYLANCE_REGION", "NA"), env["CYLANCE_TENANT_ID"],
        env["CYLANCE_APP_ID"], env["CYLANCE_APP_SECRET"],
    ),
    "huntress": lambda env: HuntressClient(
        env["HUNTRESS_API_KEY"], env["HUNTRESS_API_SECRET"],
    ),
    "freshdesk": lambda env: FreshdeskClient(
        env["FRESHDESK_DOMAIN"], env["FRESHDESK_API_KEY"],
    ),
    "unifi": lambda env: UnifiClient(
        env["UNIFI_URL"], env["UNIFI_API_KEY"],
        verify_tls=str(env.get("UNIFI_VERIFY_TLS", "")).strip().lower() in ("1", "true", "yes"),
    ),
    "proofpoint": lambda env: ProofpointClient(
        env["PROOFPOINT_REGION"], env["PROOFPOINT_USER"], env["PROOFPOINT_PASSWORD"],
    ),
    # Communication channels (D-28/D-29)
    "email": lambda env: _build_email(env),
    "msteams": lambda env: _build_msteams(env),
    # m365 is built per-client (D-33) — handled specially in ClientFactory, not here.
}


def _build_email(env: dict) -> Any:
    from .email import EmailClient
    return EmailClient(env)


def _build_msteams(env: dict) -> Any:
    from .msteams import TeamsClient
    return TeamsClient(env["TEAMS_CLIENT_ID"], env["TEAMS_CLIENT_SECRET"],
                       env["TEAMS_TENANT_ID"], service_url=env.get("TEAMS_SERVICE_URL", ""))


def _build_custom(integration: str, env: dict) -> Any:
    """Owner-defined custom integration (D-27) — generic credentialed HTTP client."""
    from ..core.custom_integrations import get_store
    from .custom import CustomHTTPClient
    ci = get_store().get(integration)
    if ci is None:
        raise ValueError(f"no client for integration '{integration}'")
    return CustomHTTPClient(ci.id, ci.base_url, ci.auth, env,
                            probe_path=ci.probe_path, read_paths=ci.read_paths,
                            verify_tls=ci.verify_tls)


class ClientFactory:
    """Builds tenant-scoped vendor clients. v1 reads creds from .env (single tenant);
    the (integration, tenant_id) signature is the seam for per-client creds in Phase 3.
    Clients are cached per (integration, tenant) so tokens/rate-limiters are shared."""

    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or get_config()
        self._cache: dict[tuple[str, str], Any] = {}

    def __call__(self, integration: str, tenant_id: str) -> Any:
        key = (integration, tenant_id)
        if key not in self._cache:
            if integration == "m365":              # per-client Graph client (D-33) — fail-closed
                from .m365 import build_m365
                self._cache[key] = build_m365(self.cfg, tenant_id)
                return self._cache[key]
            if integration == "exo":               # per-client Exchange client (D-41) — fail-closed
                from .exo import build_exo
                self._cache[key] = build_exo(self.cfg, tenant_id)
                return self._cache[key]
            if integration == "spo":               # per-client SharePoint admin client (D-89)
                from .spo import build_spo
                self._cache[key] = build_spo(self.cfg, tenant_id)
                return self._cache[key]
            builder = _BUILDERS.get(integration)
            env = credentials.require(integration, self.cfg)  # fail-closed if unconfigured
            if builder is not None:
                self._cache[key] = builder(env)
            else:                                  # custom integration (D-27) or unknown
                self._cache[key] = _build_custom(integration, env)
        return self._cache[key]

    def invalidate(self, integration: str) -> None:
        """Drop cached clients for an integration so updated credentials take effect now."""
        for key in [k for k in self._cache if k[0] == integration]:
            del self._cache[key]


def probe(integration: str, cfg: Optional[Config] = None) -> dict[str, Any]:
    """Smallest auth-proving call per integration. Never raises; returns {ok, detail, latency_ms}."""
    cfg = cfg or get_config()
    started = time.monotonic()
    # m365/exo/spo are per-client (D-33/D-41/D-89): probe the first signed-in client, else not.
    if integration in ("m365", "exo", "spo"):
        from ..core import m365_auth
        connected = m365_auth.list_connected(cfg, service=integration)
        if not connected:
            return {"ok": False, "detail": "no client signed in yet — sign one in below",
                    "latency_ms": 0}
        try:
            detail = ClientFactory(cfg)(integration, tenant_id=connected[0]).probe()
            detail["detail"] = f"client '{connected[0]}': {detail.get('detail', '')}"
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.monotonic() - started) * 1000)}
        return {"ok": bool(detail.get("ok")), "detail": detail.get("detail", ""),
                "latency_ms": int((time.monotonic() - started) * 1000)}
    try:
        client = ClientFactory(cfg)(integration, tenant_id="*")
        detail = client.probe()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.monotonic() - started) * 1000)}
    return {"ok": bool(detail.get("ok")), "detail": detail.get("detail", ""),
            "latency_ms": int((time.monotonic() - started) * 1000)}
