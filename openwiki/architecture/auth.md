---
type: Architecture
title: Authentication & OAuth
description: App-only OAuth 2.0 client credentials, token caching and refresh, credential security, and TokenProvider lifecycle.
---

# Authentication & OAuth

The spdoc-mcp server authenticates to Microsoft Graph using **OAuth 2.0 client credentials (app-only)**. This page explains the credential flow, token lifecycle, and security model.

See [ADR-0001: App-only client-credentials authentication](/spec/adr/0001-app-only-client-credentials-auth.md) for the design rationale.

## Why App-Only OAuth

The server runs unattended, invoked by an LLM agent (Claude) over MCP with no human present at tool execution time. App-only OAuth is the only authentication model that:

- Requires no interactive user sign-in or consent popup.
- Allows the server to start deterministically with just three environment variables.
- Works identically for both stdio and HTTP/SSE transports.

Alternative flows (delegated OAuth, device-code, managed identity) all require user interaction or Azure-specific hosting, which are not compatible with this use case.

**Tradeoff:** every action is attributed to the application principal, not the end user, so there is no per-user audit trail. All actions require the app's own Graph permissions, not a user's.

## Credentials Setup

### Entra ID App Registration

Create an Entra ID app registration (one-time setup):

1. Open the [Azure Portal](https://portal.azure.com/).
2. Navigate to **Azure Active Directory > App registrations**.
3. Click **New registration**.
4. Name it (e.g., "spdoc-mcp") and register.
5. Copy the **Directory (tenant) ID** and **Application (client) ID**.
6. Go to **Certificates & secrets** and create a new client secret; copy its value immediately (it's only shown once).

You now have three values:

| Name | Environment Variable | Source |
|---|---|---|
| Directory (tenant) ID | `AZURE_TENANT_ID` | App registration overview |
| Application (client) ID | `AZURE_CLIENT_ID` | App registration overview |
| Client secret value | `AZURE_CLIENT_SECRET` | Certificates & secrets (copy immediately) |

### Grant Graph Permissions

Give the app permission to read and write SharePoint metadata:

1. In the app registration, go to **API permissions**.
2. Click **Add a permission > Microsoft Graph**.
3. Select **Application permissions**.
4. Search for and add one of:
   - **`Sites.Selected`** (preferred — scoped to only specific sites) — requires additional admin consent step per site.
   - **`Sites.ReadWrite.All`** (broader, simpler initial setup).
5. Click **Grant admin consent** (requires tenant admin).

The app can now read documents and columns and update metadata on SharePoint sites within its permission scope.

### Store Credentials

Set the three environment variables in `.env` or your runtime environment:

```bash
AZURE_TENANT_ID=your-tenant-id-value
AZURE_CLIENT_ID=your-client-id-value
AZURE_CLIENT_SECRET=your-client-secret-value
```

**Security notes:**

- Never commit `.env` to version control. Add it to `.gitignore`.
- Never pass credentials as tool parameters or log them.
- The credentials file should be readable only by the process owner.

## Token Lifecycle

### Credential Flow

The OAuth 2.0 client credentials flow is stateless at the protocol level:

1. Server sends HTTP POST to `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` with:
   - `grant_type=client_credentials`
   - `client_id`, `client_secret`, `scope`
2. Microsoft Azure responds with an access token and `expires_in` (seconds until expiry).
3. Server makes Graph requests with the token in the `Authorization: Bearer {token}` header.
4. When the token expires, repeat step 1.

### TokenProvider & In-Memory Cache

The **TokenProvider** class in [auth.py](/src/spdoc_mcp/auth.py) owns the in-memory token cache and manages the acquire/refresh lifecycle. It is the **only place** in the server that holds runtime state.

```python
class TokenProvider:
    """Owns the in-memory access-token cache and its acquire/refresh lifecycle."""

    async def get_token(self) -> str:
        """Return a valid Graph access token, acquiring or refreshing only when needed."""
```

The server uses a **process-wide singleton instance** (see `get_token_provider()` in [auth.py](/src/spdoc_mcp/auth.py)):

```python
@functools.lru_cache
def get_token_provider() -> TokenProvider:
    """Return the process-wide TokenProvider singleton."""
    return TokenProvider()
```

Every tool call gets the token the same way:

```python
token_provider = get_token_provider()
token = await token_provider.get_token()
headers = {"Authorization": f"Bearer {token}"}
# Make Graph request with headers
```

### Double-Checked Locking

The `TokenProvider.get_token()` method uses **double-checked locking** so that multiple concurrent tool calls hitting a cold or near-expiry cache trigger exactly one network refresh; the rest reuse the result:

```python
async def get_token(self) -> str:
    # Fast path: no await
    if self._is_fresh():
        return self._require_token()

    # Slow path: acquire lock and re-check
    async with self._get_lock():
        if self._is_fresh():
            return self._require_token()
        await self._refresh()
        return self._require_token()
```

This avoids thundering-herd scenarios where concurrent requests all try to refresh the same expired token.

### Refresh Margin

The token is considered "expired" not when its stated `expires_in` is reached, but **300 seconds (5 minutes) before**. This covers:

- Clock skew between the client and Azure.
- In-flight requests that might use a token in its last seconds.

```python
_REFRESH_MARGIN_SECONDS = 300
```

### Monotonic Clock

The `TokenProvider` uses `time.monotonic()` by default (not wall-clock time):

```python
def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
    self._clock = clock
```

This protects against NTP/DST jumps that could prematurely expire or over-extend a token. Monotonic time is relative and immune to wall-clock adjustments.

### Event Loop Binding

The internal `asyncio.Lock` is created lazily and bound to the running event loop:

```python
def _get_lock(self) -> asyncio.Lock:
    """Return a lock bound to the current running loop, recreating it if the loop changed."""
    loop = asyncio.get_running_loop()
    if self._lock is None or self._lock_loop is not loop:
        self._lock = asyncio.Lock()
        self._lock_loop = loop
    return self._lock
```

The `TokenProvider` is a process-wide singleton that may be awaited from more than one event loop (e.g., a startup credential check, then the server's own loop). A stale lock would raise, so we recreate it if the loop changed.

## Security Model

### Credentials Never Logged

The server **never logs credentials or tokens**. When logging:

- `SecretStr` fields are automatically redacted by Pydantic.
- The token is never printed or included in structured logs.
- Auth failures are logged with code and reason, not with the secret that failed.

### Credentials Never Persisted

Tokens are cached **only in memory**. They are not written to disk, not serialized to JSON, not stored in any persistent data store.

When the process exits, the cache is lost — the next process will start with an empty cache and acquire a fresh token.

### No Manual Token Management

Tools never manage tokens directly. They call `await token_provider.get_token()` and trust it to refresh as needed. The TokenProvider handles all the mechanics.

### Configuration Standard

See `.claude/standards/configuration.md` for the full standard. Key points:

- Credentials are read **only** from `settings.py` — no scattered `os.environ` calls.
- All config is validated at startup via Pydantic, so missing credentials crash immediately with a clear error, not at the first tool call.
- Credentials are marked `SecretStr` so they are never accidentally printed.

## Testing

Tests for auth live in [test_auth.py](/tests/test_auth.py):

- **Token caching** — successful acquisition is cached and reused.
- **Refresh on expiry** — token is refreshed before stated expiry.
- **Refresh margin** — token is refreshed 300 seconds before expiry.
- **Double-checked locking** — concurrent calls don't trigger multiple refreshes.
- **Error handling** — network errors and Azure errors are surfaced cleanly.
- **Clock mock** — tests use a mocked clock to fast-forward expiry without delays.
- **Event loop isolation** — tests verify lock creation and re-binding across loops.

Run tests with:

```bash
uv run pytest tests/test_auth.py -v
```

## Next Steps

- For tool development, use the TokenProvider pattern: `token = await token_provider.get_token()`.
- If you need to call Graph directly from a tool, see the examples in [test_auth.py](/tests/test_auth.py) for httpx setup.
- If you need to support additional OAuth flows (delegated, device-code, managed identity), add them as new auth modules — the current design doesn't preclude it.

---

**Generated by OpenWiki.** See [ADR-0001](/spec/adr/0001-app-only-client-credentials-auth.md) for design rationale.
