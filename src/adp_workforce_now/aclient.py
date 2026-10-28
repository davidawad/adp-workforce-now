"""Asynchronous ADP Workforce Now client (mirror of :class:`adp_workforce_now.AdpClient`)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import date
from typing import Any

import anyio
import httpx2
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client

from . import _core as c
from .config import AdpConfig
from .errors import AdpAuthError


class AsyncAdpClient:
    """Async twin of :class:`AdpClient`; same arguments, token caching, retries and read-only
    guard. Use as ``async with AsyncAdpClient(config) as adp:``. See the sync class and the
    README for the endpoint notes (including which endpoints are unverified for Workforce Now).

    ``transport`` is an ``httpx2.AsyncBaseTransport`` for tests. ``sleep`` is an async callable.
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
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_backoff = max_backoff
        self.token_leeway = token_leeway
        self._sleep = sleep
        self._clock = clock
        self._lock = anyio.Lock()
        self._expires_at: float | None = None
        self._stale = True
        self._http = AsyncOAuth2Client(
            config.client_id,
            config.client_secret,
            token_endpoint=config.token_url,
            grant_type="client_credentials",
            base_url=config.api_base,
            timeout=timeout,
            verify=config.ssl_context(),
            transport=transport,
        )

    async def __aenter__(self) -> AsyncAdpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _token_due(self) -> bool:
        if self._stale or not self._http.token:
            return True
        return (
            self._expires_at is not None and self._clock() >= self._expires_at - self.token_leeway
        )

    async def _ensure_token(self) -> None:
        if not self._token_due():
            return
        async with self._lock:
            if not self._token_due():
                return
            try:
                token = await self._http.fetch_token(
                    self.config.token_url, grant_type="client_credentials"
                )
            except OAuthError as e:
                raise AdpAuthError(f"token request failed: {e}", body=str(e)) from e
            self._expires_at = token.get("expires_at")
            self._stale = False

    async def request(
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
        """See :meth:`AdpClient.request`. Only GET unless ``allow_writes=True``."""
        m = c.check_request(method, path, allow_writes)
        attempt = 0
        refreshed = False
        while True:
            await self._ensure_token()
            try:
                resp = await self._http.request(
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
                await self._sleep(
                    c.retry_delay(attempt, None, self.backoff_factor, self.max_backoff)
                )
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
                await self._sleep(
                    c.retry_delay(
                        attempt, c.retry_after_seconds(resp), self.backoff_factor, self.max_backoff
                    )
                )
                attempt += 1
                continue
            if status >= 400:
                raise c.api_error(resp)
            return resp

    async def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` and return the JSON object; ``{}`` on 204 / empty body."""
        return c.json_or_empty(await self.request("GET", path, params=params))

    async def paginate(
        self,
        path: str,
        key: str,
        *,
        page_size: int = c.DEFAULT_PAGE_SIZE,
        params: Mapping[str, Any] | None = None,
        **odata: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """See :meth:`AdpClient.paginate`."""
        skip = 0
        while True:
            page = await self.get(path, c.merge_params(params, top=page_size, skip=skip, **odata))
            items = page.get(key) or []
            if not items:
                return
            for item in items:
                yield item
            skip += len(items)

    def workers(
        self,
        *,
        page_size: int = c.DEFAULT_PAGE_SIZE,
        filter: str | None = None,
        select: str | None = None,
        as_of_date: date | str | None = None,
        **odata: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """See :meth:`AdpClient.workers`."""
        return self.paginate(
            "/hr/v2/workers",
            "workers",
            page_size=page_size,
            filter=filter,
            select=select,
            as_of_date=as_of_date,
            **odata,
        )

    async def worker(self, aoid: str, **odata: Any) -> dict[str, Any] | None:
        """See :meth:`AdpClient.worker`."""
        body = await self.get(f"/hr/v2/workers/{c.q(aoid)}", c.odata_params(**odata))
        workers = body.get("workers") or []
        return workers[0] if workers else None

    async def workers_meta(self) -> dict[str, Any]:
        """See :meth:`AdpClient.workers_meta`."""
        return await self.get("/hr/v2/workers/meta")

    async def time_off_balances(self, aoid: str, **odata: Any) -> dict[str, Any]:
        """See :meth:`AdpClient.time_off_balances`."""
        path = f"/time/v2/workers/{c.q(aoid)}/time-off-details/time-off-balances"
        return await self.get(path, c.odata_params(**odata))

    async def time_off_requests(
        self, aoid: str, *, start_date: date | str, end_date: date | str, **odata: Any
    ) -> dict[str, Any]:
        """See :meth:`AdpClient.time_off_requests`."""
        path = f"/time/v2/workers/{c.q(aoid)}/time-off-details/time-off-requests"
        flt = c.time_off_request_filter(start_date, end_date)
        return await self.get(path, c.odata_params(filter=flt, **odata))

    async def pay_statements(self, aoid: str, **odata: Any) -> dict[str, Any]:
        """See :meth:`AdpClient.pay_statements`. UNVERIFIED for Workforce Now."""
        path = f"/payroll/v1/workers/{c.q(aoid)}/pay-statements"
        return await self.get(path, c.odata_params(**odata))

    async def payroll_output(self, payroll_month: str, **odata: Any) -> dict[str, Any]:
        """See :meth:`AdpClient.payroll_output`. UNVERIFIED for Workforce Now."""
        flt = f"payrollMonth eq '{payroll_month}'"
        return await self.get("/payroll/v2/payroll-output", c.odata_params(filter=flt, **odata))
