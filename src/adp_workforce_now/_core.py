"""Pure helpers shared by the sync and async clients (no I/O)."""

from __future__ import annotations

import email.utils
import json
import time
from collections.abc import Iterator, Mapping
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx2

from .errors import AdpAPIError, AdpWriteNotAllowedError

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
SAFE_METHODS = frozenset({"GET"})
# OData system query options are sent with a leading "$"; ADP's own `count` and `asOfDate`
# options are not.
_DOLLAR = frozenset({"top", "skip", "filter", "select", "orderby"})
_ALIASES = {"as_of_date": "asOfDate"}
DEFAULT_PAGE_SIZE = 50  # ADP's documented default page size for GET /hr/v2/workers


def q(segment: str) -> str:
    """Percent-encode one path segment (an AOID may contain '/', spaces, ...)."""
    return quote(str(segment), safe="")


def odata_params(**odata: Any) -> dict[str, Any]:
    """Map Python keywords to ADP query parameters: ``top`` -> ``$top``, ``as_of_date`` ->
    ``asOfDate``. ``None`` values are dropped, booleans become ``true``/``false``, dates become
    ISO strings. Unknown names pass through unchanged."""
    out: dict[str, Any] = {}
    for k, v in odata.items():
        if v is None:
            continue
        name = f"${k}" if k in _DOLLAR else _ALIASES.get(k, k)
        out[name] = _scalar(v)
    return out


def _scalar(v: Any) -> Any:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, date):
        return v.isoformat()
    return v


def check_request(method: str, path: str, allow_writes: bool) -> str:
    """Validate method and path; return the upper-cased method."""
    m = method.upper()
    if m not in SAFE_METHODS and not allow_writes:
        raise AdpWriteNotAllowedError(
            f"{m} refused: this client is read-only. Writes are out of scope for adp-workforce-now "
            "0.1.x; pass allow_writes=True to request() to send one anyway."
        )
    if not path.startswith("/") or path.startswith("//"):
        # An absolute URL would send the bearer token to whatever host it names.
        raise ValueError(f"path must be relative to the API base and start with '/': {path!r}")
    return m


def retry_after_seconds(resp: httpx2.Response) -> float | None:
    """Parse ``Retry-After`` (delta-seconds, as ADP documents; HTTP-date also accepted)."""
    raw = resp.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def retry_delay(
    attempt: int, retry_after: float | None, backoff_factor: float, max_backoff: float
) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based): ``Retry-After`` if the server
    sent one, else exponential backoff. Always capped at ``max_backoff``."""
    wait = retry_after if retry_after is not None else backoff_factor * (2**attempt)
    return min(wait, max_backoff)


def should_retry(method: str, status: int) -> bool:
    # 429 means the request was not processed, so it is safe for any method; 5xx only for GET.
    return status == 429 or (status in RETRY_STATUSES and method in SAFE_METHODS)


def _body(resp: httpx2.Response) -> Any:
    try:
        return resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return resp.text or None


def _message(body: Any, resp: httpx2.Response) -> str:
    if isinstance(body, dict):
        # OAuth-style (RFC 6750) errors, as in ADP's token guide.
        if "error" in body:
            return f"{body['error']}: {body.get('error_description', '')}".rstrip(": ")
        # Best effort for ADP's confirmMessage; the exact shape is unverified.
        texts = [t for t in _walk_message_text(body.get("confirmMessage")) if t]
        if texts:
            return "; ".join(dict.fromkeys(texts))
    return f"{resp.status_code} {resp.reason_phrase}"


def _walk_message_text(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "messageTxt" and isinstance(v, str):
                yield v
            else:
                yield from _walk_message_text(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_message_text(v)


def api_error(resp: httpx2.Response) -> AdpAPIError:
    body = _body(resp)
    confirm = body.get("confirmMessage") if isinstance(body, dict) else None
    return AdpAPIError(
        f"ADP API {resp.request.method} {resp.request.url.path}: {_message(body, resp)}",
        status=resp.status_code,
        body=body,
        confirm_message=confirm,
        retry_after=retry_after_seconds(resp),
    )


def json_or_empty(resp: httpx2.Response) -> dict[str, Any]:
    """Parsed JSON object; ``{}`` for 204 / an empty body (ADP's end-of-data signal)."""
    if resp.status_code == 204 or not resp.content:
        return {}
    data = resp.json()
    return data if isinstance(data, dict) else {"value": data}


def time_off_request_filter(start: date | str, end: date | str) -> str:
    return f"requestStartdate eq {_scalar(start)} and requestEndDate eq {_scalar(end)}"


def merge_params(params: Mapping[str, Any] | None, **odata: Any) -> dict[str, Any]:
    return {**(params or {}), **odata_params(**odata)}
