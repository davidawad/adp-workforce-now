"""Shared fixtures: a throwaway self-signed client certificate and a scripted mock ADP."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import httpx2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from adp_workforce_now import AdpConfig


@dataclass
class Certs:
    cert: Path
    key: Path
    p12: Path
    p12_password: str = "synthetic-pass"


@pytest.fixture(scope="session")
def certs(tmp_path_factory: pytest.TempPathFactory) -> Certs:
    d = tmp_path_factory.mktemp("certs")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-client.invalid")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    out = Certs(d / "cert.pem", d / "key.pem", d / "bundle.p12")
    out.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    out.key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    out.p12.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"synthetic",
            key,
            cert,
            None,
            serialization.BestAvailableEncryption(out.p12_password.encode()),
        )
    )
    return out


@pytest.fixture
def config(certs: Certs) -> AdpConfig:
    return AdpConfig("client-id", "client-secret", str(certs.cert), str(certs.key))


@dataclass
class MockAdp:
    """Records requests; serves a token endpoint and a script for everything else."""

    expires_in: int = 3600
    api: list = field(default_factory=list)  # queued httpx2.Response / callables
    seen: list[httpx2.Request] = field(default_factory=list)
    tokens: int = 0
    token_status: int = 200

    def tokens_seen(self) -> list[httpx2.Request]:
        return [r for r in self.seen if r.url.path == "/auth/oauth/v2/token"]

    def api_seen(self) -> list[httpx2.Request]:
        return [r for r in self.seen if r.url.path != "/auth/oauth/v2/token"]

    def handle(self, req: httpx2.Request) -> httpx2.Response:
        self.seen.append(req)
        if req.url.path == "/auth/oauth/v2/token":
            if self.token_status != 200:
                return httpx2.Response(
                    self.token_status,
                    json={"error": "invalid_client", "error_description": "bad secret"},
                )
            self.tokens += 1
            return httpx2.Response(
                200,
                json={
                    "access_token": f"tok{self.tokens}",
                    "token_type": "Bearer",
                    "expires_in": self.expires_in,
                },
            )
        nxt = self.api.pop(0) if self.api else httpx2.Response(200, json={})
        return nxt(req) if callable(nxt) else nxt

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)


@pytest.fixture
def adp() -> MockAdp:
    return MockAdp()
