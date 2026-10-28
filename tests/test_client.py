import base64
import time
from urllib.parse import parse_qs

import httpx2
import pytest

from adp_workforce_now import (
    AdpAPIError,
    AdpAuthError,
    AdpClient,
    AdpWriteNotAllowedError,
)


def client(config, adp, **kw) -> AdpClient:
    kw.setdefault("sleep", lambda s: adp.sleeps.append(s))
    return AdpClient(config, transport=adp.transport, **kw)


@pytest.fixture(autouse=True)
def _sleeps(adp):
    adp.sleeps = []


def test_token_request_grant_and_basic_auth(config, adp) -> None:
    adp.api = [httpx2.Response(200, json={"workers": []})]
    with client(config, adp) as c:
        c.get("/hr/v2/workers")
    tok = adp.tokens_seen()[0]
    assert str(tok.url) == "https://accounts.adp.com/auth/oauth/v2/token"
    assert tok.method == "POST"
    assert parse_qs(tok.content.decode())["grant_type"] == ["client_credentials"]
    expect = "Basic " + base64.b64encode(b"client-id:client-secret").decode()
    assert tok.headers["authorization"] == expect
    assert tok.headers["content-type"].startswith("application/x-www-form-urlencoded")


def test_bearer_header_and_base_url(config, adp) -> None:
    with client(config, adp) as c:
        c.get("/hr/v2/workers")
    req = adp.api_seen()[0]
    assert req.headers["authorization"] == "Bearer tok1"
    assert str(req.url) == "https://api.adp.com/hr/v2/workers"


def test_token_cached_across_calls(config, adp) -> None:
    with client(config, adp) as c:
        for _ in range(3):
            c.get("/hr/v2/workers")
    assert len(adp.tokens_seen()) == 1


def test_token_refetched_before_expiry(config, adp) -> None:
    adp.expires_in = 30  # inside the 60 s leeway: due again immediately
    with client(config, adp) as c:
        c.get("/hr/v2/workers")
        c.get("/hr/v2/workers")
    assert [r.headers["authorization"] for r in adp.api_seen()] == ["Bearer tok1", "Bearer tok2"]


def test_token_refetched_when_clock_passes_expiry(config, adp) -> None:
    now = [time.time()]
    with client(config, adp, clock=lambda: now[0]) as c:
        c.get("/hr/v2/workers")
        c.get("/hr/v2/workers")
        assert len(adp.tokens_seen()) == 1
        now[0] += 10**9
        c.get("/hr/v2/workers")
    assert len(adp.tokens_seen()) == 2


def test_401_refetches_token_once_then_succeeds(config, adp) -> None:
    adp.api = [
        httpx2.Response(401, json={"error": "invalid_token"}),
        httpx2.Response(200, json={"ok": 1}),
    ]
    with client(config, adp) as c:
        assert c.get("/hr/v2/workers") == {"ok": 1}
    assert len(adp.tokens_seen()) == 2


def test_persistent_401_is_auth_error(config, adp) -> None:
    adp.api = [httpx2.Response(401, json={"error": "invalid_token"})] * 2
    with client(config, adp) as c, pytest.raises(AdpAuthError) as e:
        c.get("/hr/v2/workers")
    assert e.value.status == 401


def test_token_endpoint_rejection_is_auth_error(config, adp) -> None:
    adp.token_status = 401
    with client(config, adp) as c, pytest.raises(AdpAuthError, match="invalid_client"):
        c.get("/hr/v2/workers")


def test_paginate_stops_on_204(config, adp) -> None:
    adp.api = [
        httpx2.Response(200, json={"workers": [{"associateOID": "A"}, {"associateOID": "B"}]}),
        httpx2.Response(200, json={"workers": [{"associateOID": "C"}]}),
        httpx2.Response(204),
    ]
    with client(config, adp) as c:
        got = list(c.workers(page_size=2))
    assert [w["associateOID"] for w in got] == ["A", "B", "C"]
    qs = [dict(r.url.params) for r in adp.api_seen()]
    # $skip advances by items actually returned, not by the requested page size
    assert [(q["$top"], q["$skip"]) for q in qs] == [("2", "0"), ("2", "2"), ("2", "3")]


def test_paginate_odata_options(config, adp) -> None:
    adp.api = [httpx2.Response(204)]
    with client(config, adp) as c:
        list(
            c.paginate(
                "/hr/v2/workers",
                "workers",
                page_size=10,
                filter="x eq 'y'",
                select="a/b",
                as_of_date="04/18/2010",
                count=True,
            )
        )
    p = adp.api_seen()[0].url.params
    assert p["$filter"] == "x eq 'y'" and p["$select"] == "a/b"
    assert p["asOfDate"] == "04/18/2010" and p["count"] == "true" and p["$top"] == "10"


def test_paginate_stops_on_empty_page(config, adp) -> None:
    adp.api = [httpx2.Response(200, json={"workers": []})]
    with client(config, adp) as c:
        assert list(c.workers()) == []
    assert len(adp.api_seen()) == 1


def test_aoid_is_quoted_in_path(config, adp) -> None:
    with client(config, adp) as c:
        c.time_off_balances("G3/X Y")
        c.worker("a?b#c")
    paths = [r.url.raw_path for r in adp.api_seen()]
    assert paths[0] == b"/time/v2/workers/G3%2FX%20Y/time-off-details/time-off-balances"
    assert paths[1] == b"/hr/v2/workers/a%3Fb%23c"


def test_time_off_balances_204_is_empty(config, adp) -> None:
    adp.api = [httpx2.Response(204)]
    with client(config, adp) as c:
        assert c.time_off_balances("A1") == {}


def test_time_off_requests_filter(config, adp) -> None:
    import datetime as dt

    with client(config, adp) as c:
        c.time_off_requests("A1", start_date=dt.date(2026, 1, 1), end_date="2026-02-01", top=5)
    req = adp.api_seen()[0]
    assert req.url.path == "/time/v2/workers/A1/time-off-details/time-off-requests"
    assert req.url.params["$filter"] == (
        "requestStartdate eq 2026-01-01 and requestEndDate eq 2026-02-01"
    )
    assert req.url.params["$top"] == "5"


def test_worker_returns_first_or_none(config, adp) -> None:
    adp.api = [
        httpx2.Response(200, json={"workers": [{"associateOID": "A1"}]}),
        httpx2.Response(204),
    ]
    with client(config, adp) as c:
        assert c.worker("A1") == {"associateOID": "A1"}
        assert c.worker("A2") is None


def test_payroll_methods_paths(config, adp) -> None:
    with client(config, adp) as c:
        c.pay_statements("A1")
        c.payroll_output("2026-01")
    a, b = adp.api_seen()
    assert a.url.path == "/payroll/v1/workers/A1/pay-statements"
    assert b.url.path == "/payroll/v2/payroll-output"
    assert b.url.params["$filter"] == "payrollMonth eq '2026-01'"


@pytest.mark.parametrize("method", ["POST", "put", "PATCH", "DELETE", "HEAD"])
def test_non_get_refused(config, adp, method) -> None:
    with client(config, adp) as c, pytest.raises(AdpWriteNotAllowedError):
        c.request(method, "/anything")
    assert adp.seen == []  # refused before any network traffic, token included


def test_allow_writes_sends_post_without_retry_on_5xx(config, adp) -> None:
    adp.api = [httpx2.Response(503), httpx2.Response(200, json={})]
    with client(config, adp) as c, pytest.raises(AdpAPIError) as e:
        c.request("POST", "/x/y", json={"a": 1}, allow_writes=True)
    assert e.value.status == 503 and len(adp.api_seen()) == 1


def test_no_write_methods_exposed() -> None:
    assert not any(hasattr(AdpClient, m) for m in ("post", "put", "patch", "delete"))


@pytest.mark.parametrize("path", ["https://evil.example/x", "//evil.example/x", "hr/v2/workers"])
def test_path_must_be_relative_to_base(config, adp, path) -> None:
    with client(config, adp) as c, pytest.raises(ValueError):
        c.request("GET", path)
    assert adp.seen == []  # the bearer token is never sent anywhere else


def test_retry_429_honours_retry_after(config, adp) -> None:
    adp.api = [
        httpx2.Response(429, headers={"Retry-After": "7"}),
        httpx2.Response(429, headers={"Retry-After": "2"}),
        httpx2.Response(200, json={"ok": True}),
    ]
    with client(config, adp) as c:
        assert c.get("/hr/v2/workers") == {"ok": True}
    assert adp.sleeps == [7.0, 2.0]


def test_retry_5xx_uses_exponential_backoff_and_cap(config, adp) -> None:
    adp.api = [httpx2.Response(503), httpx2.Response(500), httpx2.Response(200, json={})]
    with client(config, adp, backoff_factor=1.0, max_backoff=1.5) as c:
        c.get("/hr/v2/workers")
    assert adp.sleeps == [1.0, 1.5]


def test_retry_after_capped(config, adp) -> None:
    adp.api = [httpx2.Response(429, headers={"Retry-After": "9999"}), httpx2.Response(200, json={})]
    with client(config, adp, max_backoff=5) as c:
        c.get("/hr/v2/workers")
    assert adp.sleeps == [5]


def test_retries_exhausted_raises_api_error(config, adp) -> None:
    adp.api = [httpx2.Response(429, headers={"Retry-After": "1"})] * 10
    with client(config, adp, max_retries=2) as c, pytest.raises(AdpAPIError) as e:
        c.get("/hr/v2/workers")
    assert e.value.status == 429 and e.value.retry_after == 1.0
    assert len(adp.api_seen()) == 3


def test_transport_error_retried_for_get(config, adp) -> None:
    def boom(req):
        raise httpx2.ConnectError("synthetic", request=req)

    adp.api = [boom, httpx2.Response(200, json={"ok": 1})]
    with client(config, adp) as c:
        assert c.get("/hr/v2/workers") == {"ok": 1}
    assert len(adp.sleeps) == 1


def test_error_mapping_confirm_message(config, adp) -> None:
    body = {
        "confirmMessage": {
            "confirmMessageID": {"idValue": "synthetic"},
            "resourceMessages": [
                {"processMessages": [{"userMessage": {"messageTxt": "Worker not found"}}]}
            ],
        }
    }
    adp.api = [httpx2.Response(404, json=body)]
    with client(config, adp) as c, pytest.raises(AdpAPIError) as e:
        c.worker("nope")
    err = e.value
    assert err.status == 404 and "Worker not found" in str(err)
    assert err.confirm_message == body["confirmMessage"] and err.body == body


def test_error_mapping_oauth_style_and_plain_text(config, adp) -> None:
    adp.api = [
        httpx2.Response(
            403, json={"error": "insufficient_scope", "error_description": "Unauthorized Web API"}
        ),
        httpx2.Response(400, text="not json"),
    ]
    with client(config, adp) as c:
        with pytest.raises(AdpAPIError, match="insufficient_scope: Unauthorized Web API") as e:
            c.get("/a")
        assert e.value.status == 403
        with pytest.raises(AdpAPIError) as e2:
            c.get("/b")
        assert e2.value.status == 400 and e2.value.body == "not json"


def test_timeout_configurable(config, adp) -> None:
    c = client(config, adp, timeout=3.5)
    assert c._http.timeout.read == 3.5
    c.close()
