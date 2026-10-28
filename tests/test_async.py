import httpx2
import pytest

from adp_workforce_now import AdpAPIError, AdpWriteNotAllowedError, AsyncAdpClient


def client(config, adp, **kw) -> AsyncAdpClient:
    async def sleep(s: float) -> None:
        adp.sleeps.append(s)

    return AsyncAdpClient(config, transport=adp.transport, sleep=sleep, **kw)


@pytest.fixture(autouse=True)
def _sleeps(adp):
    adp.sleeps = []


async def test_token_bearer_and_cache(config, adp) -> None:
    async with client(config, adp) as c:
        await c.get("/hr/v2/workers")
        await c.get("/hr/v2/workers")
    assert len(adp.tokens_seen()) == 1
    assert {r.headers["authorization"] for r in adp.api_seen()} == {"Bearer tok1"}


async def test_token_refresh_before_expiry(config, adp) -> None:
    adp.expires_in = 30
    async with client(config, adp) as c:
        await c.get("/a")
        await c.get("/b")
    assert len(adp.tokens_seen()) == 2


async def test_paginate_204(config, adp) -> None:
    adp.api = [httpx2.Response(200, json={"workers": [{"n": 1}, {"n": 2}]}), httpx2.Response(204)]
    async with client(config, adp) as c:
        got = [w async for w in c.workers(page_size=2)]
    assert got == [{"n": 1}, {"n": 2}]


async def test_path_quoting_and_guard(config, adp) -> None:
    async with client(config, adp) as c:
        await c.time_off_balances("G3/X Y")
        with pytest.raises(AdpWriteNotAllowedError):
            await c.request("DELETE", "/x")
    assert adp.api_seen()[0].url.raw_path.startswith(b"/time/v2/workers/G3%2FX%20Y/")


async def test_retry_429_and_error_mapping(config, adp) -> None:
    adp.api = [
        httpx2.Response(429, headers={"Retry-After": "3"}),
        httpx2.Response(200, json={"ok": 1}),
        httpx2.Response(404, json={"confirmMessage": {"x": 1}}),
    ]
    async with client(config, adp) as c:
        assert await c.get("/a") == {"ok": 1}
        with pytest.raises(AdpAPIError) as e:
            await c.get("/b")
    assert adp.sleeps == [3.0] and e.value.confirm_message == {"x": 1}


async def test_p12_config_builds_client(certs, adp) -> None:
    from adp_workforce_now import AdpConfig

    cfg = AdpConfig("i", "s", p12_file=str(certs.p12), p12_password=certs.p12_password)
    async with AsyncAdpClient(cfg, transport=adp.transport) as c:
        await c.get("/x")
    assert adp.tokens == 1
