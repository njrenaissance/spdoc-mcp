"""Unit tests for the app-only Graph auth layer.

Only the two true external boundaries are faked: the network (via ``respx``, which patches
httpx's transport so the provider's real request-building and parsing still run) and time
(via an injected ``FakeClock``, so expiry/refresh behaviour is deterministic with no sleep).
Credentials flow from the real settings module, proving they are never parameters.
"""

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest
import respx

from spdoc_mcp.auth import TokenProvider, get_token_provider
from spdoc_mcp.errors import AuthError
from spdoc_mcp.settings import get_settings

TENANT = "test-tenant"
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret-value"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

EXPIRES_IN = 3600
# Provider refreshes 300 s early, so the cached token stays fresh only while clock < 3300.
FRESH_LIMIT = EXPIRES_IN - 300
# Acquire once, then one silent refresh once the cached token goes stale.
REFRESH_CALLS = 2


class FakeClock:
    """Deterministic, injectable stand-in for ``time.monotonic``."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate credentials from the environment and reset both cached singletons."""
    monkeypatch.setenv("AZURE_TENANT_ID", TENANT)
    monkeypatch.setenv("AZURE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AZURE_CLIENT_SECRET", CLIENT_SECRET)
    get_settings.cache_clear()
    get_token_provider.cache_clear()
    yield
    get_settings.cache_clear()
    get_token_provider.cache_clear()


def _token_response(token: str = "access-token", expires_in: int = EXPIRES_IN) -> httpx.Response:
    return httpx.Response(200, json={"access_token": token, "expires_in": expires_in})


@pytest.mark.unit
async def test_acquires_token_on_first_call() -> None:
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(return_value=_token_response("first-token"))
        token = await TokenProvider(clock=FakeClock()).get_token()

    assert token == "first-token"
    assert route.call_count == 1


@pytest.mark.unit
async def test_reuses_cached_token_within_validity() -> None:
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(return_value=_token_response("cached-token"))
        provider = TokenProvider(clock=FakeClock())
        first = await provider.get_token()
        second = await provider.get_token()

    assert first == second == "cached-token"
    assert route.call_count == 1


@pytest.mark.unit
async def test_request_uses_settings_credentials_and_client_credentials_flow() -> None:
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(return_value=_token_response())
        await TokenProvider(clock=FakeClock()).get_token()
        request = route.calls.last.request

    assert str(request.url) == TOKEN_URL
    body = dict(parse_qsl(request.content.decode()))
    assert body["grant_type"] == "client_credentials"
    assert body["scope"] == GRAPH_SCOPE
    assert body["client_id"] == CLIENT_ID
    assert body["client_secret"] == CLIENT_SECRET


@pytest.mark.unit
@pytest.mark.parametrize(
    "advance",
    [
        pytest.param(FRESH_LIMIT + 100, id="within_refresh_margin"),
        pytest.param(EXPIRES_IN + 100, id="after_expiry"),
    ],
)
async def test_refreshes_when_stale(advance: float) -> None:
    clock = FakeClock()
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(side_effect=[_token_response("old-token"), _token_response("new-token")])
        provider = TokenProvider(clock=clock)
        first = await provider.get_token()
        clock.advance(advance)
        second = await provider.get_token()

    assert first == "old-token"
    assert second == "new-token"
    assert route.call_count == REFRESH_CALLS


@pytest.mark.unit
async def test_no_refresh_just_inside_margin() -> None:
    clock = FakeClock()
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(return_value=_token_response("stable-token"))
        provider = TokenProvider(clock=clock)
        await provider.get_token()
        clock.advance(FRESH_LIMIT - 1)
        token = await provider.get_token()

    assert token == "stable-token"
    assert route.call_count == 1


@pytest.mark.unit
async def test_short_lived_token_is_cached_with_clamped_margin() -> None:
    short_expiry = 100  # <= the 300 s refresh margin, so the margin must clamp to half the lifetime
    clock = FakeClock()
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(
            side_effect=[
                _token_response("short-1", expires_in=short_expiry),
                _token_response("short-2", expires_in=short_expiry),
            ]
        )
        provider = TokenProvider(clock=clock)
        first = await provider.get_token()
        clock.advance(short_expiry / 2 - 1)  # still inside the clamped (lifetime / 2) window
        cached = await provider.get_token()
        clock.advance(short_expiry)  # now past the refresh deadline
        refreshed = await provider.get_token()

    assert first == cached == "short-1"
    assert refreshed == "short-2"
    assert route.call_count == REFRESH_CALLS


@pytest.mark.unit
def test_get_token_survives_across_event_loops() -> None:
    clock = FakeClock()
    with respx.mock:
        respx.post(TOKEN_URL).mock(side_effect=[_token_response("loop-1"), _token_response("loop-2")])
        provider = TokenProvider(clock=clock)
        first = asyncio.run(provider.get_token())  # lock is created and bound to this loop
        clock.advance(EXPIRES_IN + 100)  # force the next call to re-acquire the lock
        second = asyncio.run(provider.get_token())  # a different loop must not raise

    assert first == "loop-1"
    assert second == "loop-2"


@pytest.mark.unit
async def test_concurrent_callers_refresh_once() -> None:
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(return_value=_token_response("shared-token"))
        provider = TokenProvider(clock=FakeClock())
        results = await asyncio.gather(*[provider.get_token() for _ in range(5)])

    assert results == ["shared-token"] * 5
    assert route.call_count == 1


@pytest.mark.unit
async def test_network_error_raises_auth_error() -> None:
    with respx.mock:
        respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("connection refused"))
        with pytest.raises(AuthError) as excinfo:
            await TokenProvider(clock=FakeClock()).get_token()

    assert isinstance(excinfo.value.__cause__, httpx.HTTPError)


@pytest.mark.unit
@pytest.mark.parametrize("status", [pytest.param(401, id="unauthorized"), pytest.param(500, id="server_error")])
async def test_http_status_error_raises_auth_error(status: int) -> None:
    with respx.mock:
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(status, json={"error": "denied"}))
        with pytest.raises(AuthError) as excinfo:
            await TokenProvider(clock=FakeClock()).get_token()

    assert isinstance(excinfo.value.__cause__, httpx.HTTPStatusError)


@pytest.mark.unit
@pytest.mark.parametrize(
    "response",
    [
        pytest.param(httpx.Response(200, json={"expires_in": EXPIRES_IN}), id="missing_access_token"),
        pytest.param(httpx.Response(200, text="not-json"), id="non_json_body"),
        pytest.param(
            httpx.Response(200, json={"access_token": None, "expires_in": EXPIRES_IN}), id="null_access_token"
        ),
        pytest.param(httpx.Response(200, json={"access_token": "", "expires_in": EXPIRES_IN}), id="empty_access_token"),
    ],
)
async def test_malformed_response_raises_auth_error(response: httpx.Response) -> None:
    with respx.mock:
        respx.post(TOKEN_URL).mock(return_value=response)
        with pytest.raises(AuthError) as excinfo:
            await TokenProvider(clock=FakeClock()).get_token()

    assert CLIENT_SECRET not in str(excinfo.value)


@pytest.mark.unit
async def test_repr_hides_secret_and_token() -> None:
    with respx.mock:
        respx.post(TOKEN_URL).mock(return_value=_token_response("secret-token"))
        provider = TokenProvider(clock=FakeClock())
        await provider.get_token()

    rendered = repr(provider)
    assert "secret-token" not in rendered
    assert CLIENT_SECRET not in rendered


@pytest.mark.unit
def test_get_token_provider_is_cached() -> None:
    first = get_token_provider()
    second = get_token_provider()
    assert first is second
    get_token_provider.cache_clear()
    assert get_token_provider() is not first


@pytest.mark.unit
def test_constructor_rejects_credential_parameters() -> None:
    with pytest.raises(TypeError):
        TokenProvider(client_secret="should-not-be-accepted")  # type: ignore[call-arg]
