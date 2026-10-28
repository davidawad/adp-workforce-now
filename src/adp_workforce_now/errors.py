"""Exceptions raised by adp_workforce_now."""

from __future__ import annotations

from typing import Any


class AdpError(Exception):
    """Base class for every error raised by this package."""


class AdpConfigError(AdpError, ValueError):
    """The configuration is incomplete or inconsistent."""


class AdpWriteNotAllowedError(AdpError):
    """A non-GET request was attempted without ``allow_writes=True``."""


class AdpAuthError(AdpError):
    """Authentication failed: the token request was rejected, or the API kept answering 401."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class AdpAPIError(AdpError):
    """The ADP API answered with an HTTP error status.

    ``confirm_message`` is the ``confirmMessage`` object from the JSON error body when ADP sent
    one (the shape is not documented in the guides this package was written against, so it is
    passed through untouched). ``body`` is the parsed JSON body, or the raw text.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        body: Any = None,
        confirm_message: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.confirm_message = confirm_message
        self.retry_after = retry_after
