"""Synchronous ADP Workforce Now client."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import date
from typing import Any

import httpx2
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import OAuth2Client

from . import _core as c
from .config import AdpConfig
from .errors import AdpAuthError


class AdpClient:
    """Read-first client for the ADP Workforce Now APIs.

    Token handling: the OAuth2 client-credentials token (mutual TLS, ``expires_in`` 3600 s by
    default per ADP) is cached and re-fetched ``token_leeway`` seconds before it expires, and once
    after a 401. ADP asks that a new token not be requested for every call.

    Retries: HTTP 429 (honouring ``Retry-After``) and 500/502/503/504 for GET, plus transport
    errors for GET, up to ``max_retries`` times with exponential backoff capped at
    ``max_backoff`` seconds.

    Writes are out of scope: :meth:`request` refuses any method but GET unless
    ``allow_writes=True`` is passed, and no write endpoint is provided.

    ``transport`` (an ``httpx2`` transport, e.g. ``httpx2.MockTransport``) replaces the network
    for tests; the mutual-TLS context is still built from the config.
    """

    def __init__(
        self,
        config: AdpConfig,
        *,
        timeout: float | httpx2.Timeout = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        max_backoff: float = 60.0,
        token_leeway: float = 60.0,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_backoff = max_backoff
        self.token_leeway = token_leeway
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._expires_at: float | None = None
        self._stale = True
        self._http = OAuth2Client(
            config.client_id,
            config.client_secret,
            token_endpoint=config.token_url,
            grant_type="client_credentials",
            base_url=config.api_base,
            timeout=timeout,
            verify=config.ssl_context(),
            transport=transport,
        )

    # -- lifecycle -------------------------------------------------------------------------
    def __enter__(self) -> AdpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- token -----------------------------------------------------------------------------
    def _token_due(self) -> bool:
        if self._stale or not self._http.token:
            return True
        return (
            self._expires_at is not None and self._clock() >= self._expires_at - self.token_leeway
        )

    def _ensure_token(self) -> None:
        if not self._token_due():
            return
        with self._lock:
            if not self._token_due():
                return
            try:
                token = self._http.fetch_token(
                    self.config.token_url, grant_type="client_credentials"
                )
            except OAuthError as e:
                raise AdpAuthError(f"token request failed: {e}", body=str(e)) from e
            self._expires_at = token.get("expires_at")
            self._stale = False

    # -- generic ---------------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        data: Any = None,
        allow_writes: bool = False,
    ) -> httpx2.Response:
        """Send a request to ``path`` (relative to ``api_base``) and return the 2xx response
        (including 204). Raises :class:`AdpAPIError` for other statuses.

        Only GET is allowed unless ``allow_writes=True``.
        """
        m = c.check_request(method, path, allow_writes)
        attempt = 0
        refreshed = False
        while True:
            self._ensure_token()
            try:
                resp = self._http.request(
                    m,
                    path,
                    auth=self._http.token_auth,  # we own expiry; skip Authlib's own check
                    params=params,
                    headers=headers,
                    json=json,
                    data=data,
                )
            except httpx2.TransportError:
                if attempt >= self.max_retries or m not in c.SAFE_METHODS:
                    raise
                self._sleep(c.retry_delay(attempt, None, self.backoff_factor, self.max_backoff))
                attempt += 1
                continue
            status = resp.status_code
            if status == 401:
                if not refreshed:
                    refreshed, self._stale = True, True
                    continue
                err = c.api_error(resp)
                raise AdpAuthError(str(err), status=401, body=err.body)
            if c.should_retry(m, status) and attempt < self.max_retries:
                delay = c.retry_delay(
                    attempt, c.retry_after_seconds(resp), self.backoff_factor, self.max_backoff
                )
                self._sleep(delay)
                attempt += 1
                continue
            if status >= 400:
                raise c.api_error(resp)
            return resp

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` and return the JSON object; ``{}`` on 204 / empty body."""
        return c.json_or_empty(self.request("GET", path, params=params))

    def paginate(
        self,
        path: str,
        key: str,
        *,
        page_size: int = c.DEFAULT_PAGE_SIZE,
        params: Mapping[str, Any] | None = None,
        **odata: Any,
    ) -> Iterator[dict[str, Any]]:
        """Yield every item under ``key`` from a ``$top``/``$skip`` paged collection.

        ``odata`` keywords map to query options (``filter`` -> ``$filter``, ``select`` ->
        ``$select``, ``orderby``, ``as_of_date`` -> ``asOfDate``, ``count``). Paging stops at
        HTTP 204 (ADP's documented end-of-data signal) or an empty page. ``$skip`` advances by
        the number of items actually returned, so a server-side page cap below ``page_size`` is
        handled.
        """
        skip = 0
        while True:
            page = self.get(path, c.merge_params(params, top=page_size, skip=skip, **odata))
            items = page.get(key) or []
            if not items:
                return
            yield from items
            skip += len(items)

    # -- Workforce Now: HR -----------------------------------------------------------------
    def workers(
        self,
        *,
        page_size: int = c.DEFAULT_PAGE_SIZE,
        filter: str | None = None,
        select: str | None = None,
        as_of_date: date | str | None = None,
        **odata: Any,
    ) -> Iterator[dict[str, Any]]:
        """Every worker (``GET /hr/v2/workers``). Scope: ``worker.read``.

        Verified: ADP's Worker Management guide for Workforce Now lists this path, ``$top``,
        ``$skip``, ``$select``, ``$filter`` (``/hr/v2/workers/meta`` lists the supported filters)
        and the 204 end-of-data response.
        """
        return self.paginate(
            "/hr/v2/workers",
            "workers",
            page_size=page_size,
            filter=filter,
            select=select,
            as_of_date=as_of_date,
            **odata,
        )

    def worker(self, aoid: str, **odata: Any) -> dict[str, Any] | None:
        """One worker by associate OID (``GET /hr/v2/workers/{aoid}``), or None on 204.

        Returns the first element of the response's ``workers`` array. The path is verified;
        the single-worker response envelope is assumed to match the list response (unverified).
        """
        body = self.get(f"/hr/v2/workers/{c.q(aoid)}", c.odata_params(**odata))
        workers = body.get("workers") or []
        return workers[0] if workers else None

    def workers_meta(self) -> dict[str, Any]:
        """``GET /hr/v2/workers/meta``: supported filters and fields for the workers call."""
        return self.get("/hr/v2/workers/meta")

    # -- Workforce Now: time ---------------------------------------------------------------
    def time_off_balances(self, aoid: str, **odata: Any) -> dict[str, Any]:
        """Time-off balances for one worker, as of today by default.

        ``GET /time/v2/workers/{aoid}/time-off-details/time-off-balances``.
        Scope: ``timeOffBalance.read``. Returns ``{}`` on 204, which ADP documents as "no active
        time-off policy assignment as of the date" (for active or terminated workers alike).
        """
        path = f"/time/v2/workers/{c.q(aoid)}/time-off-details/time-off-balances"
        return self.get(path, c.odata_params(**odata))

    def time_off_requests(
        self,
        aoid: str,
        *,
        start_date: date | str,
        end_date: date | str,
        **odata: Any,
    ) -> dict[str, Any]:
        """Time-off requests for one worker in a date window.

        ``GET /time/v2/workers/{aoid}/time-off-details/time-off-requests`` with the mandatory
        ``$filter=requestStartdate eq <date> and requestEndDate eq <date>`` (ADP's Workforce Now
        Time Off Request guide). Scope: ``timeOffRequest.read``. ``$top``/``$skip`` are
        accepted via ``odata``. Returns ``{}`` on 204.

        Not covered: the European "ADP Workforce" product uses a different, v3 path with a
        different ``$filter``; call :meth:`get` directly for it.
        """
        path = f"/time/v2/workers/{c.q(aoid)}/time-off-details/time-off-requests"
        flt = c.time_off_request_filter(start_date, end_date)
        return self.get(path, c.odata_params(filter=flt, **odata))

    # -- Payroll (UNVERIFIED for Workforce Now) --------------------------------------------
    def pay_statements(self, aoid: str, **odata: Any) -> dict[str, Any]:
        """Pay statements for one worker (``GET /payroll/v1/workers/{aoid}/pay-statements``).

        Scope: ``payStatement.read``. UNVERIFIED for Workforce Now: ADP's guide for this path is
        for ADP LINK, and that guide says OData parameters are not supported there. Check the
        API Explorer for your subscription before relying on it.
        """
        path = f"/payroll/v1/workers/{c.q(aoid)}/pay-statements"
        return self.get(path, c.odata_params(**odata))

    def payroll_output(self, payroll_month: str, **odata: Any) -> dict[str, Any]:
        """Payroll output for a month (``GET /payroll/v2/payroll-output``).

        ``payroll_month`` is sent as ``$filter=payrollMonth eq '<value>'`` (``YYYY-MM``; ADP LINK
        also accepts ``MM-YYYY``). Scope: ``payrollOutput.read``. UNVERIFIED for Workforce Now:
        the guide is for ADP LINK. Check the API Explorer for your subscription before relying
        on it.
        """
        flt = f"payrollMonth eq '{payroll_month}'"
        return self.get("/payroll/v2/payroll-output", c.odata_params(filter=flt, **odata))
