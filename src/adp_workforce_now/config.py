"""Configuration and mutual-TLS context construction."""

from __future__ import annotations

import os
import ssl
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from .errors import AdpConfigError

TOKEN_URL = "https://accounts.adp.com/auth/oauth/v2/token"
API_BASE = "https://api.adp.com"


@dataclass(frozen=True)
class AdpConfig:
    """Credentials and endpoints for one ADP API Central / Marketplace client.

    Provide the client certificate either as PEM files (``cert_file`` and ``key_file``) or as a
    PKCS#12 bundle (``p12_file`` and, if it is encrypted, ``p12_password``).

    ADP documents mutual TLS for both the token endpoint and the API gateway. Hosts for the
    European ESI platform differ (``accounts.eu.adp.com`` / ``api.eu.adp.com``); set
    ``token_url`` and ``api_base`` for that.
    """

    client_id: str
    client_secret: str = field(repr=False)
    cert_file: str | None = None
    key_file: str | None = None
    p12_file: str | None = None
    p12_password: str | None = field(default=None, repr=False)
    api_base: str = API_BASE
    token_url: str = TOKEN_URL

    def __post_init__(self) -> None:
        has_pem = bool(self.cert_file and self.key_file)
        if not self.client_id or not self.client_secret:
            raise AdpConfigError("client_id and client_secret are required")
        if has_pem == bool(self.p12_file):
            raise AdpConfigError(
                "give cert_file + key_file (PEM) or p12_file (PKCS#12), not both or neither"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AdpConfig:
        """Build a config from ``ADP_CLIENT_ID``, ``ADP_CLIENT_SECRET``, ``ADP_CERT_FILE``,
        ``ADP_KEY_FILE``, ``ADP_P12_FILE``, ``ADP_P12_PASSWORD``, ``ADP_API_BASE`` and
        ``ADP_TOKEN_URL`` (the last two optional)."""
        e = os.environ if env is None else env
        missing = [k for k in ("ADP_CLIENT_ID", "ADP_CLIENT_SECRET") if not e.get(k)]
        if missing:
            raise AdpConfigError(f"missing environment: {', '.join(missing)}")
        return cls(
            client_id=e["ADP_CLIENT_ID"],
            client_secret=e["ADP_CLIENT_SECRET"],
            cert_file=e.get("ADP_CERT_FILE") or None,
            key_file=e.get("ADP_KEY_FILE") or None,
            p12_file=e.get("ADP_P12_FILE") or None,
            p12_password=e.get("ADP_P12_PASSWORD") or None,
            api_base=e.get("ADP_API_BASE") or API_BASE,
            token_url=e.get("ADP_TOKEN_URL") or TOKEN_URL,
        )

    def ssl_context(self) -> ssl.SSLContext:
        """Default CA verification plus the ADP client certificate (mutual TLS)."""
        ctx = ssl.create_default_context()
        if self.p12_file:
            _load_p12(ctx, self.p12_file, self.p12_password)
        else:
            assert self.cert_file and self.key_file
            ctx.load_cert_chain(self.cert_file, self.key_file)
        return ctx


def _load_p12(ctx: ssl.SSLContext, path: str, password: str | None) -> None:
    """Load a PKCS#12 bundle into ``ctx``.

    ``ssl`` can only read PEM files, so the bundle is re-encoded to a PEM file (private key
    encrypted with a throwaway password) in a private temporary directory that is removed
    straight after loading.
    """
    key, cert, extra = pkcs12.load_key_and_certificates(
        Path(path).read_bytes(), password.encode() if password else None
    )
    if key is None or cert is None:
        raise AdpConfigError(f"{path}: PKCS#12 bundle has no private key and certificate")
    throwaway = os.urandom(24).hex()
    pem = cert.public_bytes(serialization.Encoding.PEM)
    pem += b"".join(c.public_bytes(serialization.Encoding.PEM) for c in extra or [])
    pem += key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(throwaway.encode()),
    )
    with tempfile.TemporaryDirectory(prefix="adp-p12-") as d:
        f = Path(d) / "bundle.pem"
        f.write_bytes(pem)
        ctx.load_cert_chain(str(f), password=throwaway)
