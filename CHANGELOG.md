# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-28

### Added
- `AdpConfig` with PEM (cert + key) or PKCS#12 client certificates, and `AdpConfig.from_env()`.
- Sync `AdpClient` and async `AsyncAdpClient` on Authlib's OAuth2 client over httpx2, with
  mutual TLS on token and API calls, token caching and refresh before expiry.
- Read endpoints: workers (list, get, meta), time-off balances, time-off requests,
  pay statements and payroll output (the last two unverified for Workforce Now).
- `paginate()` ($top/$skip, stops on 204 or an empty page) and a generic `request()`.
- Retry on 429/5xx with backoff honouring `Retry-After`; configurable timeout.
- Typed exceptions `AdpAuthError`, `AdpAPIError`, `AdpWriteNotAllowedError`, `AdpConfigError`.
- `request()` refuses non-GET methods unless `allow_writes=True`; no write endpoints are shipped.
