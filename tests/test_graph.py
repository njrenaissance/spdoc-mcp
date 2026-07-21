"""Unit tests for the Microsoft Graph client wrapper (issue #8).

These are TDD tests authored in the inner Plan phase: they pin the contract of the
not-yet-written ``spdoc_mcp.graph`` module. Only the true external boundary — the network —
is faked, via ``respx`` (which patches httpx's transport so the client's real request-building
and error-translation run). The token provider is a *real* collaborator constructed for the
test (never mocked, per testing.md); its own boundary (the token endpoint) is the only other
thing respx stands in for. This proves every Graph request is authorized with a token the auth
layer produced, using the same fixtures shape as ``test_auth.py``.

Two plan decisions this suite deliberately pins (see spec/plans/issue-8-graph-client.md → "Open
decisions"):
  * OData query options use the ``$`` prefix Graph actually requires (``$expand``/``$filter``),
    not the shorthand the spec table writes (``expand=fields``).
  * A library *name* is resolved via ``$filter=displayName eq '<name>'``; a GUID passes through
    with no request.
Both are the natural place for the human to overrule at the HITL gate; the tests would change
with the decision, not the other way around.
"""

import json

import httpx
import pytest
import respx

from spdoc_mcp.auth import TokenProvider, get_token_provider
from spdoc_mcp.errors import GraphError, NotFoundError
from spdoc_mcp.graph import GRAPH_BASE_URL, GraphClient, get_graph_client
from spdoc_mcp.settings import get_settings

TENANT = "test-tenant"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
GRAPH_TOKEN = "graph-token"

SITE_ID = "contoso.sharepoint.com,00000000-0000-0000-0000-000000000001,00000000-0000-0000-0000-000000000002"
LIST_ID = "11111111-1111-1111-1111-111111111111"
ITEM_ID = "42"

SITE_URL = "https://contoso.sharepoint.com/sites/Marketing"
# Graph resolves a site URL as {hostname}:/{server-relative-path} (spec: GET /sites/{hostname}:/{site-path}).
EXPECTED_SITE_PATH = "/v1.0/sites/contoso.sharepoint.com:/sites/Marketing"

HTTP_BAD_REQUEST = 400
HTTP_SERVICE_UNAVAILABLE = 503


class FixedClock:
    """A frozen clock so the real TokenProvider never treats its test token as stale."""

    def __call__(self) -> float:
        return 0.0


@pytest.fixture(autouse=True)
def _graph_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide credentials from the environment and reset the cached singletons around each test."""
    monkeypatch.setenv("AZURE_TENANT_ID", TENANT)
    monkeypatch.setenv("AZURE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "test-client-secret-value")
    get_settings.cache_clear()
    get_token_provider.cache_clear()
    get_graph_client.cache_clear()
    yield
    get_settings.cache_clear()
    get_token_provider.cache_clear()
    get_graph_client.cache_clear()


@pytest.fixture
def client() -> GraphClient:
    """A GraphClient backed by a real TokenProvider with a frozen clock."""
    return GraphClient(token_provider=TokenProvider(clock=FixedClock()))


def _mock_token() -> None:
    """Register the token endpoint used by the real provider (must be added before the catch-all)."""
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": GRAPH_TOKEN, "expires_in": 3600}))


def _mock_graph(response: httpx.Response) -> respx.Route:
    """Register a catch-all Graph route returning ``response``; returns the route for assertions."""
    return respx.route(host="graph.microsoft.com").mock(return_value=response)


# --------------------------------------------------------------------------------------------------
# Each of the five operations issues the exact Graph v1.0 request and returns parsed JSON (AC #1, #2).
# --------------------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_resolve_site_from_url_issues_hostname_path_request(client: GraphClient) -> None:
    body = {"id": SITE_ID, "displayName": "Marketing"}
    with respx.mock:
        _mock_token()
        route = _mock_graph(httpx.Response(200, json=body))
        result = await client.resolve_site(SITE_URL)

    assert result == body
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == EXPECTED_SITE_PATH


@pytest.mark.unit
async def test_resolve_site_from_site_id_passes_id_through(client: GraphClient) -> None:
    body = {"id": SITE_ID}
    with respx.mock:
        _mock_token()
        route = _mock_graph(httpx.Response(200, json=body))
        result = await client.resolve_site(SITE_ID)

    assert result == body
    assert route.calls.last.request.url.path == f"/v1.0/sites/{SITE_ID}"


@pytest.mark.unit
async def test_resolve_list_returns_guid_without_a_request(client: GraphClient) -> None:
    with respx.mock:
        _mock_token()
        route = _mock_graph(httpx.Response(200, json={}))
        result = await client.resolve_list(SITE_ID, LIST_ID)

    assert result == LIST_ID
    assert route.call_count == 0


@pytest.mark.unit
async def test_resolve_list_resolves_name_via_displayname_filter(client: GraphClient) -> None:
    body = {"value": [{"id": LIST_ID, "displayName": "Documents"}]}
    with respx.mock:
        _mock_token()
        route = _mock_graph(httpx.Response(200, json=body))
        result = await client.resolve_list(SITE_ID, "Documents")

    assert result == LIST_ID
    request = route.calls.last.request
    assert request.url.path == f"/v1.0/sites/{SITE_ID}/lists"
    assert request.url.params.get("$filter") == "displayName eq 'Documents'"


@pytest.mark.unit
async def test_resolve_list_raises_not_found_when_name_has_no_match(client: GraphClient) -> None:
    with respx.mock:
        _mock_token()
        _mock_graph(httpx.Response(200, json={"value": []}))
        with pytest.raises(NotFoundError) as excinfo:
            await client.resolve_list(SITE_ID, "Nonexistent")

    assert "Nonexistent" in str(excinfo.value)


@pytest.mark.unit
async def test_list_documents_expands_fields(client: GraphClient) -> None:
    body = {"value": [{"id": ITEM_ID, "fields": {"Title": "Q3 Plan"}}]}
    with respx.mock:
        _mock_token()
        route = _mock_graph(httpx.Response(200, json=body))
        result = await client.list_documents(SITE_ID, LIST_ID)

    assert result == body
    request = route.calls.last.request
    assert request.url.path == f"/v1.0/sites/{SITE_ID}/lists/{LIST_ID}/items"
    assert request.url.params.get("$expand") == "fields"


@pytest.mark.unit
async def test_get_document_metadata_reads_item_fields(client: GraphClient) -> None:
    body = {"Title": "Q3 Plan", "Status": "Draft"}
    with respx.mock:
        _mock_token()
        route = _mock_graph(httpx.Response(200, json=body))
        result = await client.get_document_metadata(SITE_ID, LIST_ID, ITEM_ID)

    assert result == body
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v1.0/sites/{SITE_ID}/lists/{LIST_ID}/items/{ITEM_ID}/fields"


@pytest.mark.unit
async def test_update_document_metadata_patches_item_fields(client: GraphClient) -> None:
    fields = {"Status": "Approved", "Reviewer": "jdoe"}
    with respx.mock:
        _mock_token()
        route = _mock_graph(httpx.Response(200, json=fields))
        result = await client.update_document_metadata(SITE_ID, LIST_ID, ITEM_ID, fields)

    assert result == fields
    request = route.calls.last.request
    assert request.method == "PATCH"
    assert request.url.path == f"/v1.0/sites/{SITE_ID}/lists/{LIST_ID}/items/{ITEM_ID}/fields"
    assert json.loads(request.content) == fields


@pytest.mark.unit
async def test_list_columns_reads_columns(client: GraphClient) -> None:
    body = {"value": [{"name": "Status", "displayName": "Status", "choice": {"choices": ["Draft", "Approved"]}}]}
    with respx.mock:
        _mock_token()
        route = _mock_graph(httpx.Response(200, json=body))
        result = await client.list_columns(SITE_ID, LIST_ID)

    assert result == body
    assert route.calls.last.request.url.path == f"/v1.0/sites/{SITE_ID}/lists/{LIST_ID}/columns"


# --------------------------------------------------------------------------------------------------
# Every request is authorized with a token from the auth layer (AC #4).
# --------------------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_requests_carry_bearer_token_from_auth_layer(client: GraphClient) -> None:
    with respx.mock:
        _mock_token()
        route = _mock_graph(httpx.Response(200, json={"id": SITE_ID}))
        await client.resolve_site(SITE_ID)

    assert route.calls.last.request.headers["Authorization"] == f"Bearer {GRAPH_TOKEN}"


# --------------------------------------------------------------------------------------------------
# Non-2xx responses become domain exceptions carrying status + Graph detail, never a stack trace (AC #3).
# --------------------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_bad_request_raises_graph_error_with_status_and_detail(client: GraphClient) -> None:
    """A choice column rejecting an out-of-set value: Graph 400, surfaced cleanly (spec acceptance criterion)."""
    error_body = {"error": {"code": "invalidRequest", "message": "'Nope' is not a valid choice."}}
    with respx.mock:
        _mock_token()
        _mock_graph(httpx.Response(HTTP_BAD_REQUEST, json=error_body))
        with pytest.raises(GraphError) as excinfo:
            await client.update_document_metadata(SITE_ID, LIST_ID, ITEM_ID, {"Status": "Nope"})

    err = excinfo.value
    assert err.status_code == HTTP_BAD_REQUEST
    assert err.graph_code == "invalidRequest"
    assert "invalidRequest" in str(err)
    assert "not a valid choice" in str(err)


@pytest.mark.unit
async def test_not_found_response_raises_not_found_error_with_detail(client: GraphClient) -> None:
    error_body = {"error": {"code": "itemNotFound", "message": "The requested item was not found."}}
    with respx.mock:
        _mock_token()
        _mock_graph(httpx.Response(404, json=error_body))
        with pytest.raises(NotFoundError) as excinfo:
            await client.get_document_metadata(SITE_ID, LIST_ID, ITEM_ID)

    assert "itemNotFound" in str(excinfo.value)


@pytest.mark.unit
async def test_non_json_error_body_degrades_to_status_only_graph_error(client: GraphClient) -> None:
    """A malformed (non-JSON) error body must not crash the translator — it degrades to status only."""
    with respx.mock:
        _mock_token()
        _mock_graph(httpx.Response(HTTP_SERVICE_UNAVAILABLE, text="<html>Service Unavailable</html>"))
        with pytest.raises(GraphError) as excinfo:
            await client.list_columns(SITE_ID, LIST_ID)

    err = excinfo.value
    assert err.status_code == HTTP_SERVICE_UNAVAILABLE
    assert err.graph_code is None


# --------------------------------------------------------------------------------------------------
# The client is available as a process-wide singleton, like the token provider (established pattern).
# --------------------------------------------------------------------------------------------------


@pytest.mark.unit
def test_get_graph_client_is_cached() -> None:
    first = get_graph_client()
    second = get_graph_client()
    assert first is second
    get_graph_client.cache_clear()
    assert get_graph_client() is not first


@pytest.mark.unit
def test_graph_base_url_is_v1() -> None:
    assert GRAPH_BASE_URL == "https://graph.microsoft.com/v1.0"
