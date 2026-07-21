"""App-only Microsoft Graph authentication (ADR-0001).

Acquires OAuth 2.0 access tokens via the client-credentials flow, using the three
env-sourced credentials from :mod:`spdoc_mcp.settings`. Tokens are cached in memory and
silently refreshed near expiry; this cache is the server's only runtime state, owned here.

No token or secret is ever persisted to disk or logged. Credentials are read from the
settings module only — never accepted as parameters. The Graph client (the sole consumer)
awaits :meth:`TokenProvider.get_token` for a bearer token string on each request.
"""

import asyncio
import functools
import time
from collections.abc import Callable

import httpx

from spdoc_mcp.errors import AuthError
from spdoc_mcp.settings import get_settings

_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
# Refresh this many seconds before the stated expiry, to cover clock skew and in-flight requests.
_REFRESH_MARGIN_SECONDS = 300
_TOKEN_REQUEST_TIMEOUT_SECONDS = 30


class TokenProvider:
    """Owns the in-memory access-token cache and its acquire/refresh lifecycle.

    Constructed without credentials — those are read fresh from the settings module at
    fetch time and never stored on the instance. The single process-wide instance is
    obtained via :func:`get_token_provider`.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        # Monotonic by default: expires_in is a relative duration, so a monotonic clock is
        # immune to wall-clock/NTP/DST jumps that could prematurely expire or over-extend a token.
        self._clock = clock
        self._access_token: str | None = None
        self._refresh_at: float | None = None
        # The lock is created lazily inside the running loop (see _get_lock): an asyncio.Lock
        # binds to the first loop that awaits it, and this provider is a process-wide singleton
        # that may outlive the loop it was first used on.
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    async def get_token(self) -> str:
        """Return a valid Graph access token, acquiring or refreshing only when needed.

        Uses double-checked locking so that concurrent callers hitting a cold or
        near-expiry cache trigger exactly one network refresh; the rest reuse the result.
        """
        # Fast path: no await, so it cannot interleave under asyncio's single-threaded loop.
        if self._is_fresh():
            return self._require_token()

        async with self._get_lock():
            # Re-check: another coroutine may have refreshed while we awaited the lock.
            if self._is_fresh():
                return self._require_token()
            await self._refresh()
            return self._require_token()

    def _get_lock(self) -> asyncio.Lock:
        """Return a lock bound to the current running loop, recreating it if the loop changed.

        The provider is a cached singleton, so it may be awaited from more than one event loop
        over the process lifetime (e.g. a startup credential check, then the server's own loop);
        a lock bound to a stale loop would raise. This runs synchronously with no await, so it
        cannot interleave within a single loop.
        """
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _is_fresh(self) -> bool:
        """True when a cached token exists and has not yet reached its refresh deadline."""
        if self._access_token is None or self._refresh_at is None:
            return False
        return self._clock() < self._refresh_at

    def _require_token(self) -> str:
        """Return the cached token, asserting the invariant that a fresh path set it."""
        if self._access_token is None:  # pragma: no cover - invariant guard
            raise AuthError("Token cache is unexpectedly empty after refresh")
        return self._access_token

    async def _refresh(self) -> None:
        """Fetch a new token and record the deadline at which it must be refreshed."""
        token, expires_in = await self._fetch_token()
        # Clamp the refresh margin to at most half the token's lifetime, so a short-lived
        # token is still cached for a while instead of being treated as stale on arrival.
        margin = min(_REFRESH_MARGIN_SECONDS, expires_in / 2)
        self._access_token = token
        self._refresh_at = self._clock() + expires_in - margin

    async def _fetch_token(self) -> tuple[str, int]:
        """Perform the client-credentials request and return (access_token, expires_in).

        The only place credentials are touched — into locals, never onto ``self``. Any
        acquisition or parsing failure is translated into :class:`AuthError`, chained from
        the underlying error so the traceback keeps the real root cause.
        """
        azure = get_settings().azure
        url = _TOKEN_URL_TEMPLATE.format(tenant=azure.tenant_id.get_secret_value())
        form = {
            "grant_type": "client_credentials",
            "client_id": azure.client_id.get_secret_value(),
            "client_secret": azure.client_secret.get_secret_value(),
            "scope": _GRAPH_DEFAULT_SCOPE,
        }
        try:
            async with httpx.AsyncClient(timeout=_TOKEN_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(url, data=form)
            response.raise_for_status()
        except httpx.HTTPError as err:
            raise AuthError("Failed to acquire Microsoft Graph token") from err
        return self._parse_token(response)

    @staticmethod
    def _parse_token(response: httpx.Response) -> tuple[str, int]:
        """Extract (access_token, expires_in) from a token response.

        A missing field or a non-JSON/non-object body degrades to a chained
        :class:`AuthError` rather than leaking a raw ``KeyError``/``ValueError``
        (``json.JSONDecodeError`` is a ``ValueError`` subclass).
        """
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("token response was not a JSON object")
            access_token = payload["access_token"]
            if not isinstance(access_token, str) or not access_token:
                raise ValueError("token response had no usable access_token")
            return access_token, int(payload["expires_in"])
        except (KeyError, ValueError, TypeError) as err:
            raise AuthError("Malformed token response from Microsoft Graph") from err

    def __repr__(self) -> str:
        # Explicit repr so no token/secret/expiry material can leak via a default dump.
        return f"<TokenProvider cached={self._access_token is not None}>"


@functools.lru_cache
def get_token_provider() -> TokenProvider:
    """Return the process-wide :class:`TokenProvider` singleton.

    One instance means one shared token cache — the single cache owner ADR-0001 mandates,
    shared identically across the stdio and HTTP/SSE transports.
    """
    return TokenProvider()
