# adp-workforce-now

A typed Python client for the ADP Workforce Now APIs (API Central / ADP Marketplace).
Read endpoints first: mutual TLS, OAuth2 client credentials with token caching, `$top`/`$skip`
paging, retries with `Retry-After`, sync and async.

There is no maintained Python SDK for these APIs. ADP's own `adp-connection` and `adp-userinfo`
stopped at 1.0.1 in 2016, and their GitHub repositories no longer exist. (`pyadp` on PyPI is an
unrelated project.) This package is independent and is not affiliated with or endorsed by ADP.

Built on [Authlib](https://docs.authlib.org/) (OAuth2 client) over [httpx2](https://github.com/pydantic/httpx2),
plus `cryptography` for PKCS#12 bundles.

## Install

```sh
pip install adp-workforce-now      # or: uv add adp-workforce-now
```

Python 3.11 or newer (httpx2 2.15+ requires it).

## Quickstart

```python
from adp_workforce_now import AdpClient, AdpConfig

config = AdpConfig(
    client_id="...",
    client_secret="...",
    cert_file="client-cert.pem",  # PEM certificate ADP issued
    key_file="client.key",  # PEM private key you generated
    # or: p12_file="client.pfx", p12_password="..."
)
# or: config = AdpConfig.from_env()

with AdpClient(config) as adp:
    for worker in adp.workers(select="workers/associateOID,workers/person/legalName"):
        print(worker["associateOID"])

    balances = adp.time_off_balances("G3EXAMPLEAOID")  # {} on 204
    requests = adp.time_off_requests(
        "G3EXAMPLEAOID", start_date="2026-01-01", end_date="2026-01-31"
    )
```

Async:

```python
from adp_workforce_now import AsyncAdpClient

async with AsyncAdpClient(AdpConfig.from_env()) as adp:
    async for worker in adp.workers():
        ...
    balances = await adp.time_off_balances("G3EXAMPLEAOID")
```

### Environment variables for `AdpConfig.from_env()`

| Variable | Meaning |
| --- | --- |
| `ADP_CLIENT_ID`, `ADP_CLIENT_SECRET` | OAuth2 client credentials (required) |
| `ADP_CERT_FILE`, `ADP_KEY_FILE` | PEM certificate and private key |
| `ADP_P12_FILE`, `ADP_P12_PASSWORD` | PKCS#12 bundle instead of the PEM pair |
| `ADP_API_BASE` | default `https://api.adp.com` |
| `ADP_TOKEN_URL` | default `https://accounts.adp.com/auth/oauth/v2/token` |

Give a PEM pair or a PKCS#12 file, not both. The European ESI platform uses
`https://accounts.eu.adp.com/auth/oauth/v2/token` and `https://api.eu.adp.com`.

## API

| Call | Endpoint | Scope |
| --- | --- | --- |
| `workers(page_size, filter, select, as_of_date, **odata)` | `GET /hr/v2/workers` | `worker.read` |
| `worker(aoid)` | `GET /hr/v2/workers/{aoid}` | `worker.read` |
| `workers_meta()` | `GET /hr/v2/workers/meta` | `worker.read` |
| `time_off_balances(aoid)` | `GET /time/v2/workers/{aoid}/time-off-details/time-off-balances` | `timeOffBalance.read` |
| `time_off_requests(aoid, start_date, end_date)` | `GET /time/v2/workers/{aoid}/time-off-details/time-off-requests` | `timeOffRequest.read` |
| `pay_statements(aoid)` (unverified for Workforce Now) | `GET /payroll/v1/workers/{aoid}/pay-statements` | `payStatement.read` |
| `payroll_output(payroll_month)` (unverified for Workforce Now) | `GET /payroll/v2/payroll-output` | `payrollOutput.read` |
| `paginate(path, key, page_size=50, **odata)` | any `$top`/`$skip` collection | n/a |
| `get(path, params)` / `request(method, path, ...)` | any path under the API base | n/a |

Full scope strings are in `adp_workforce_now.scopes`. OData keywords map as `top`, `skip`,
`filter`, `select`, `orderby` to `$top` and so on; `as_of_date` to `asOfDate`; `count` and
other names pass through unchanged.

Behaviour:

- Token: fetched on first use, cached, re-fetched `token_leeway` (60 s) before `expires_in`, and
  once more after a 401.
- Retries: 429 (waiting `Retry-After` seconds when sent) and 500/502/503/504 for GET, plus
  transport errors for GET; `max_retries=3`, exponential `backoff_factor=0.5`, capped by
  `max_backoff=60`. `timeout=30` seconds (a float or `httpx2.Timeout`).
- `paginate` stops at HTTP 204 or an empty page, and advances `$skip` by the number of items
  actually returned.
- Errors: `AdpAuthError` (token request rejected, or 401 after a fresh token), `AdpAPIError`
  (`status`, `body`, `confirm_message` from the JSON `confirmMessage` object, `retry_after`),
  `AdpWriteNotAllowedError`, `AdpConfigError`. Transport failures that survive the retries
  propagate as `httpx2` errors.

### Writes are out of scope

Version 0.1.0 reads only. No write endpoint is provided (not Pay Data Input, not time-off
balance modify, not time-off request submit). `request()` raises `AdpWriteNotAllowedError` for
any method other than GET unless you pass `allow_writes=True`, in which case it sends whatever
you give it, with no retry on 5xx. `path` must be relative to the API base and start with `/`, so
the bearer token cannot be sent to another host.

## ADP API Central setup

Condensed from ADP's guides (links under Sources); follow ADP's current documentation for your
subscription.

1. Create a project in the ADP Developer Self-Service portal. Its "Development API Credentials"
   section holds the client ID and client secret.
2. Get a mutual-TLS (Web Services) certificate, by either route:
   - Self-service: in the portal choose Certificate, then Request Certificate. Copy and save the
     private key when it is shown (it cannot be retrieved later), then download the `.pem`
     certificate. It is valid two years and works for all your projects.
   - Your own CSR:
     ```sh
     openssl genrsa -out company_auth.key 2048
     openssl req -new -key company_auth.key -out company_auth.csr
     ```
     Leave the challenge password blank, do not request S/MIME capabilities, and ask your ADP
     representative for the Organization Name and Common Name to use (keep them: renewal must
     match exactly). Submit the CSR through an ADP eService ticket or ADP's Certificate Signing
     Tool (profile "Authentication and Transaction Signing"). The signed certificate can take up
     to a week and expires after two years. Never send the private key to ADP.
   - Optional PKCS#12 (`.pfx`) bundle, which this package also reads:
     `openssl pkcs12 -export -out company_auth.pfx -inkey company_auth.key -in company_auth.pem`
3. Add the canonical scope URIs for the APIs you need (`adp_workforce_now.scopes`) to the
   Consumer Application Registry for the subscription. Scopes are not sent in the token request;
   a missing one gives HTTP 403 `insufficient_scope`.

## What was verified against ADP's documentation

Checked on 2026-10-28 against ADP's published PDFs on `marketplace-cdn.adp.com`. The
`developers.adp.com` API Explorer is a JavaScript application that could not be read
programmatically, so no claim below rests on it.

Verified:

- Token endpoint and grant: `POST https://accounts.adp.com/auth/oauth/v2/token`,
  `grant_type=client_credentials`, `application/x-www-form-urlencoded`, client ID and secret as
  HTTP Basic or as form fields (both documented; this package uses Basic).
  [US guide](https://marketplace-cdn.adp.com/dev-portal/pdf/protected/How_to_retrieve_Client_Credentials_of_a_Subscriber),
  [Access Token chapter](https://marketplace-cdn.adp.com/dev-portal/pdf/ADP_Marketplace_ESI_-_Getting_started_chapter_3)
  (EU host `accounts.eu.adp.com`).
- Mutual TLS on both token and API calls: "all calls made to https://accounts.adp.com and
  https://api.adp.com are done using your Mutual SSL Certificate" (US guide above); the token
  request and "all Web API requests" must use the X.509 certificate (Access Token chapter).
- Certificate format: PEM CSR; PEM key and certificate; PKCS#12/`.pfx` via `openssl pkcs12
  -export` "can be used for mutual SSL authentication"; two-year validity.
  [CSR chapter](https://marketplace-cdn.adp.com/dev-portal/pdf/ADP_Marketplace_ESI_-_Getting_started_chapter_2),
  [self-service certificate guide](https://marketplace-cdn.adp.com/dev-portal/pdf/protected/Generating_a_Certificate_Signing_Request_for_Partners_chapter_2).
- Token lifetime: `expires_in` 3600 by default (60 minutes, negotiable at registration); do not
  request a token per call (Access Token chapter above).
- Errors for API calls: 400 `invalid_request`, 401 `invalid_token`, 403 `insufficient_scope`,
  500, 503 (Access Token chapter). Throttling: HTTP 429 with a `Retry-After` header in seconds
  (ESI [Getting started guide](https://marketplace-cdn.adp.com/dev-portal/pdf/ADP_Marketplace_ESI_-_Getting_started)).
- Workers and paging: `GET /hr/v2/workers`, `/hr/v2/workers/meta`, `/hr/v2/workers/{associate-oid}`;
  scope `.../workerProfileManagement/worker.read`; `$top`, `$skip`, `$select`, `$filter`
  (supported filters are listed by `/meta`), `count=true`, `asOfDate`; a synchronous call returns
  at most 50 records by default; repeat until HTTP 204 No Content.
  [Worker Management API Guide for ADP Workforce Now](https://marketplace-cdn.adp.com/dev-portal/pdf/protected/Worker_Management_API_Guide_for_ADP_Workforce_Now).
- Time-off balances: `GET /time/v2/workers/{aoid}/time-off-details/time-off-balances`, scope
  `.../timeOffBalancesManagement/timeOffBalance.read`, as of today by default; HTTP 204 when the
  worker has no active time-off policy assignment.
  [Time Off Balances API Guide for ADP Workforce Now](https://marketplace-cdn.adp.com/dev-portal/pdf/protected/Time_Off_Balances_API_Guide_for_ADP_Workforce_Now).
- Time-off requests (read): `GET /time/v2/workers/{aoid}/time-off-details/time-off-requests`,
  scope `.../timeOffRequestManagement/timeOffRequest.read`, required
  `$filter=requestStartdate eq YYYY-MM-DD and requestEndDate eq YYYY-MM-DD`.
  [Time Off Request API Guide for ADP Workforce Now](https://marketplace-cdn.adp.com/dev-portal/pdf/protected/Time_Off_Request_API_Guide_for_ADP_Workforce_Now).
  The European "ADP Workforce" product has a different v3 path
  ([guide](https://marketplace-cdn.adp.com/dev-portal/pdf/Time_Off_Request_API_Guide_for_ADP_Workforce));
  not wrapped.

Unverified (treat as such):

- Pay statements and payroll output for Workforce Now. ADP documents
  `GET /payroll/v1/workers/{aoid}/pay-statements` (scope `payStatement.read`) in the
  [ADP LINK Pay Statements guide](https://marketplace-cdn.adp.com/dev-portal/pdf/Pay_Statements_API_Guide_for_ADP_LINK)
  and `GET /payroll/v2/payroll-output` with a mandatory `payrollMonth` filter (scope
  `payrollOutput.read`) in the
  [ADP LINK Payroll Output guide](https://marketplace-cdn.adp.com/dev-portal/pdf/Payroll_Output_API_Guide_for_ADP_Link).
  No Workforce Now guide for either could be retrieved, so the two methods are provided but
  unverified for Workforce Now.
- The JSON shape of ADP error bodies. The `confirmMessage` extraction is best effort.
- The single-worker response envelope (assumed `{"workers": [...]}`) and the maximum `$top` for
  Workforce Now (only the 50 default is documented; the 1,000 limit applies to the asynchronous
  `Prefer: respond-async` mode, which this package does not implement and which ADP says Workforce
  Now Next Gen does not support).
- Whether your subscription's token endpoint prefers HTTP Basic or form-field client
  authentication. Both are documented; Basic is used.
- That a given ADP product edition exposes these endpoints at all: availability depends on your
  ADP product, API Central subscription and CAR scopes.

## Development

```sh
just install   # uv sync
just test      # pytest, no network, synthetic data only
just lint      # ruff check + format --check
just build     # uv build + twine check
```

Tests use `httpx2.MockTransport` and a throwaway self-signed certificate generated at test time.

## License

MIT. Copyright (c) 2026 David Awad.
