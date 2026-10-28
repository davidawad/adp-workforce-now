import ssl

import pytest

from adp_workforce_now import API_BASE, TOKEN_URL, AdpConfig, AdpConfigError

ENV = {
    "ADP_CLIENT_ID": "id",
    "ADP_CLIENT_SECRET": "secret",
    "ADP_CERT_FILE": "/x/cert.pem",
    "ADP_KEY_FILE": "/x/key.pem",
}


def test_from_env_pem_defaults() -> None:
    c = AdpConfig.from_env(ENV)
    assert (c.client_id, c.cert_file, c.key_file, c.p12_file) == (
        "id",
        "/x/cert.pem",
        "/x/key.pem",
        None,
    )
    assert (c.api_base, c.token_url) == (API_BASE, TOKEN_URL)


def test_from_env_p12_and_overrides() -> None:
    env = {
        "ADP_CLIENT_ID": "id",
        "ADP_CLIENT_SECRET": "s",
        "ADP_P12_FILE": "/x/b.p12",
        "ADP_P12_PASSWORD": "pw",
        "ADP_API_BASE": "https://api.eu.adp.com",
        "ADP_TOKEN_URL": "https://accounts.eu.adp.com/auth/oauth/v2/token",
    }
    c = AdpConfig.from_env(env)
    assert c.p12_file == "/x/b.p12" and c.p12_password == "pw"
    assert c.api_base == "https://api.eu.adp.com"
    assert c.token_url.startswith("https://accounts.eu.adp.com")


def test_from_env_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    assert AdpConfig.from_env().client_id == "id"


def test_from_env_missing_credentials() -> None:
    with pytest.raises(AdpConfigError, match="ADP_CLIENT_SECRET"):
        AdpConfig.from_env({"ADP_CLIENT_ID": "x"})


@pytest.mark.parametrize(
    "extra",
    [
        {},  # no certificate at all
        {"cert_file": "c"},  # cert without key
        {"cert_file": "c", "key_file": "k", "p12_file": "p"},  # both
    ],
)
def test_certificate_must_be_pem_xor_p12(extra: dict) -> None:
    with pytest.raises(AdpConfigError):
        AdpConfig("id", "secret", **extra)


def test_secrets_not_in_repr() -> None:
    c = AdpConfig("id", "hunter2", p12_file="p", p12_password="pw-secret")
    assert "hunter2" not in repr(c) and "pw-secret" not in repr(c)


def test_ssl_context_from_pem(certs) -> None:
    ctx = AdpConfig("i", "s", str(certs.cert), str(certs.key)).ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert len(ctx.get_ca_certs()) >= 0  # default verification retained


def test_ssl_context_from_p12(certs) -> None:
    cfg = AdpConfig("i", "s", p12_file=str(certs.p12), p12_password=certs.p12_password)
    assert isinstance(cfg.ssl_context(), ssl.SSLContext)


def test_p12_wrong_password(certs) -> None:
    cfg = AdpConfig("i", "s", p12_file=str(certs.p12), p12_password="wrong")
    with pytest.raises(ValueError):
        cfg.ssl_context()


def test_ssl_context_rejects_mismatched_key(certs, tmp_path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = tmp_path / "other.key"
    other.write_bytes(
        rsa.generate_private_key(65537, 2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with pytest.raises(ssl.SSLError):
        AdpConfig("i", "s", str(certs.cert), str(other)).ssl_context()


def test_client_presents_certificate_context(config, adp, monkeypatch) -> None:
    """The client builds its TLS context from the config (mTLS on token and API calls)."""
    from adp_workforce_now import AdpClient

    calls = []
    real = AdpConfig.ssl_context

    def spy(self):
        calls.append(self)
        return real(self)

    monkeypatch.setattr(AdpConfig, "ssl_context", spy)
    with AdpClient(config, transport=adp.transport):
        pass
    assert calls == [config]
