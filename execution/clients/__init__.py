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
from .huntress import HuntressClient
from .kaseya import KaseyaClient

_BUILDERS: dict[str, Callable[[dict], Any]] = {
    "kaseya": lambda env: KaseyaClient(
        env["KASEYA_BASE_URL"], env.get("KASEYA_USER"), env.get("KASEYA_PASSWORD"),
        token=env.get("KASEYA_TOKEN"),
    ),
    "cylance": lambda env: CylanceClient(
        env.get("CYLANCE_REGION", "NA"), env["CYLANCE_TENANT_ID"],
        env["CYLANCE_APP_ID"], env["CYLANCE_APP_SECRET"],
    ),
    "huntress": lambda env: HuntressClient(
        env["HUNTRESS_API_KEY"], env["HUNTRESS_API_SECRET"],
    ),
}


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
            builder = _BUILDERS.get(integration)
            if builder is None:
                raise ValueError(f"no client for integration '{integration}'")
            env = credentials.require(integration, self.cfg)  # fail-closed if unconfigured
            self._cache[key] = builder(env)
        return self._cache[key]


def probe(integration: str, cfg: Optional[Config] = None) -> dict[str, Any]:
    """Smallest auth-proving call per integration. Never raises; returns {ok, detail, latency_ms}."""
    cfg = cfg or get_config()
    started = time.monotonic()
    try:
        client = ClientFactory(cfg)(integration, tenant_id="*")
        detail = client.probe()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.monotonic() - started) * 1000)}
    return {"ok": bool(detail.get("ok")), "detail": detail.get("detail", ""),
            "latency_ms": int((time.monotonic() - started) * 1000)}
