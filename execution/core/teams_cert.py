"""Teams bot certificate auth (D-29 amendment) — generate + use an app certificate.

Instead of a client secret, the Azure app registration can authenticate with a
certificate: we generate a self-signed RSA cert ON THIS BOX, the owner uploads the
PUBLIC half (.cer) in Entra → App registrations → Certificates & secrets, and token
requests then carry a JWT client assertion signed with the private key.

The private key NEVER leaves the server: it is written to one PEM file (key + cert)
at mode 0600, gitignored (*.pem), and the API only ever returns the public
certificate + thumbprint. Requires `cryptography` (already a dependency via Fernet,
D-25) and PyJWT for the RS256 assertion (already a dependency).
"""
from __future__ import annotations

import base64
import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .config import Config, get_config

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _PROJECT_ROOT / "teams_bot_cert.pem"
_VALID_DAYS = 730                     # Entra caps uploaded certs at 2 years anyway


def cert_path(cfg: Optional[Config] = None) -> Path:
    cfg = cfg or get_config()
    return Path(cfg.get("MSPAI_TEAMS_CERT_PATH") or _DEFAULT_PATH)


def generate(cfg: Optional[Config] = None) -> dict[str, Any]:
    """Create a new RSA-2048 self-signed cert; overwrite any existing one. Returns
    info + the PUBLIC certificate PEM (safe to download/upload to Entra)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MSP AI Teams Bot")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=_VALID_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))

    key_pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    path = cert_path(cfg)
    import os
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key_pem + cert_pem)
    finally:
        os.close(fd)
    return info(cfg) | {"public_pem": cert_pem.decode()}


def _load(cfg: Optional[Config] = None):
    """(private_key_pem_bytes, certificate) or None if no cert file exists."""
    from cryptography import x509
    path = cert_path(cfg)
    if not path.is_file():
        return None
    blob = path.read_bytes()
    try:
        cert = x509.load_pem_x509_certificate(blob)
    except ValueError:
        return None
    return blob, cert


def info(cfg: Optional[Config] = None) -> dict[str, Any]:
    """Fingerprint-style status — never includes the private key."""
    loaded = _load(cfg)
    if loaded is None:
        return {"exists": False}
    _blob, cert = loaded
    der = cert.public_bytes_der() if hasattr(cert, "public_bytes_der") else None
    if der is None:
        from cryptography.hazmat.primitives import serialization
        der = cert.public_bytes(serialization.Encoding.DER)
    thumb = hashlib.sha1(der).hexdigest().upper()
    return {"exists": True, "thumbprint": thumb,
            "not_after": cert.not_valid_after_utc.isoformat(timespec="seconds")
            if hasattr(cert, "not_valid_after_utc")
            else cert.not_valid_after.isoformat(timespec="seconds"),
            "subject": cert.subject.rfc4514_string(),
            "path": str(cert_path(cfg))}


def public_pem(cfg: Optional[Config] = None) -> Optional[str]:
    loaded = _load(cfg)
    if loaded is None:
        return None
    from cryptography.hazmat.primitives import serialization
    return loaded[1].public_bytes(serialization.Encoding.PEM).decode()


def delete(cfg: Optional[Config] = None) -> bool:
    path = cert_path(cfg)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def exists(cfg: Optional[Config] = None) -> bool:
    return _load(cfg) is not None


def client_assertion(client_id: str, tenant_id: str, cfg: Optional[Config] = None) -> str:
    """Signed JWT proving possession of the app certificate (Entra client-credentials
    flow, client_assertion_type=jwt-bearer). Raises ValueError if no cert exists."""
    loaded = _load(cfg)
    if loaded is None:
        raise ValueError("no Teams bot certificate — generate one on the integration card")
    blob, cert = loaded
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(blob, password=None)
    der = cert.public_bytes(serialization.Encoding.DER)
    x5t = base64.urlsafe_b64encode(hashlib.sha1(der).digest()).rstrip(b"=").decode()
    now = int(time.time())
    claims = {
        "aud": f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        "iss": client_id, "sub": client_id,
        "jti": uuid.uuid4().hex, "nbf": now - 60, "exp": now + 600,
    }
    import jwt as pyjwt
    return pyjwt.encode(claims, key, algorithm="RS256", headers={"x5t": x5t})
