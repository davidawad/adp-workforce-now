"""A typed, read-first client for the ADP Workforce Now APIs (API Central / ADP Marketplace)."""

from .aclient import AsyncAdpClient
from .client import AdpClient
from .config import API_BASE, TOKEN_URL, AdpConfig
from .errors import (
    AdpAPIError,
    AdpAuthError,
    AdpConfigError,
    AdpError,
    AdpWriteNotAllowedError,
)

__version__ = "0.1.0"

__all__ = [
    "API_BASE",
    "TOKEN_URL",
    "AdpAPIError",
    "AdpAuthError",
    "AdpClient",
    "AdpConfig",
    "AdpConfigError",
    "AdpError",
    "AdpWriteNotAllowedError",
    "AsyncAdpClient",
    "__version__",
]
