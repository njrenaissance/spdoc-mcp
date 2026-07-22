"""Unit tests for the ``list_columns`` discovery tool (issue #10).

Two layers, mirroring the project's testing discipline (``.claude/standards/testing.md``):

* **Normalization** — the pure ``columnDefinition`` → :class:`ColumnInfo` projection is
  tested directly, with no I/O, so field-type detection, choice/taxonomy extraction, and
  the hidden/read-only filter are pinned deterministically.
* **End to end** — the tool is driven through ``fastmcp.Client`` against a real
  :class:`GraphClient` + :class:`TokenProvider`; only the network boundary is faked with
  ``respx`` (the token endpoint and the Graph requests). This proves registration,
  identifier resolution, filtering, and clean error surfacing all compose.
"""

from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from spdoc_mcp.auth import TokenProvider, get_token_provider
from spdoc_mcp.graph import GRAPH_BASE_URL, get_graph_client
from spdoc_mcp.server import create_app
from spdoc_mcp.settings import get_settings
from spdoc_mcp.tools.list_columns import (
    ColumnInfo,
    _detect_field_type,
    _is_settable,
    _to_column_info,
)

TENANT = "test-tenant"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
GRAPH_TOKEN = "graph-token"

SITE_ID = "contoso.sharepoint.com,00000000-0000-0000-0000-000000000001,00000000-0000-0000-0000-000000000002"
LIST_ID = "11111111-1111-1111-1111-111111111111"
LIBRARY_NAME = "Marketing Documents"
TERM_SET_ID = "22222222-2222-2222-2222-222222222222"

SITE_URL_MOCK = f"{GRAPH_BASE_URL}/sites/{SITE_ID}"
LISTS_URL_MOCK = f"{GRAPH_BASE_URL}/sites/{SITE_ID}/lists"
COLUMNS_PATH = f"/sites/{SITE_ID}/lists/{LIST_ID}/columns"
COLUMNS_URL_MOCK = f"{GRAPH_BASE_URL}{COLUMNS_PATH}"


class FixedClock:
    """A frozen clock so the real TokenProvider never treats its test token as stale."""

    def __call__(self) -> float:
        return 0.0


@pytest.fixture(autouse=True)
def _graph_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide credentials and reset the cached singletons around each test."""
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


@pytest.fixture(autouse=True)
def _real_token_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Back the shared GraphClient with a real TokenProvider on a frozen clock."""
    monkeypatch.setattr(
        "spdoc_mcp.graph.get_token_provider",
        lambda: TokenProvider(clock=FixedClock()),
    )


def _mock_token() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": GRAPH_TOKEN, "expires_in": 3600}))


async def _call(site: str, library: str) -> list[dict[str, Any]]:
    """Invoke the tool through a FastMCP client and return its output as plain dicts."""
    async with Client(create_app()) as client:
        result = await client.call_tool("list_columns", {"site": site, "library": library})
    return [_as_dict(item) for item in result.data]


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    return dict(vars(item))


# --------------------------------------------------------------------------------------------------
# Normalization: raw columnDefinition -> ColumnInfo (pure, no I/O).
# --------------------------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("column", "expected_type"),
    [
        pytest.param({"name": "Title", "text": {}}, "text", id="text"),
        pytest.param({"name": "Count", "number": {}}, "number", id="number"),
        pytest.param({"name": "Due", "dateTime": {}}, "dateTime", id="date_time"),
        pytest.param({"name": "Flag", "boolean": {}}, "boolean", id="boolean"),
        pytest.param({"name": "Status", "choice": {"choices": ["A"]}}, "choice", id="choice"),
        pytest.param({"name": "Owner", "personOrGroup": {}}, "personOrGroup", id="person"),
        pytest.param({"name": "Dept", "term": {}}, "taxonomy", id="term_is_taxonomy"),
        pytest.param({"name": "Mystery"}, "unknown", id="no_facet_is_unknown"),
    ],
)
def test_detect_field_type(column: dict[str, Any], expected_type: str) -> None:
    assert _detect_field_type(column) == expected_type


@pytest.mark.unit
def test_choice_column_carries_display_name_type_and_allowed_values() -> None:
    column = {
        "name": "Status",
        "displayName": "Approval Status",
        "choice": {"choices": ["Draft", "Approved"]},
    }

    info = _to_column_info(column)

    assert info == ColumnInfo(
        internal_name="Status",
        display_name="Approval Status",
        field_type="choice",
        allowed_values=["Draft", "Approved"],
        term_set_id=None,
    )


@pytest.mark.unit
def test_taxonomy_column_carries_term_set_id_not_allowed_values() -> None:
    column = {
        "name": "Department",
        "displayName": "Department",
        "term": {"termSet": {"id": TERM_SET_ID}},
    }

    info = _to_column_info(column)

    assert info.field_type == "taxonomy"
    assert info.term_set_id == TERM_SET_ID
    assert info.allowed_values is None


@pytest.mark.unit
def test_display_name_falls_back_to_internal_name_when_absent() -> None:
    info = _to_column_info({"name": "InternalOnly", "text": {}})

    assert info.display_name == "InternalOnly"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("column", "settable"),
    [
        pytest.param({"name": "Status"}, True, id="plain_column"),
        pytest.param({"name": "Status", "hidden": True}, False, id="hidden_excluded"),
        pytest.param({"name": "Created", "readOnly": True}, False, id="read_only_excluded"),
        pytest.param({"name": "Status", "hidden": False, "readOnly": False}, True, id="explicit_false"),
    ],
)
def test_is_settable_filters_system_columns(column: dict[str, Any], settable: bool) -> None:
    assert _is_settable(column) is settable


# --------------------------------------------------------------------------------------------------
# End to end through the FastMCP client.
# --------------------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_returns_user_defined_columns_for_site_and_library() -> None:
    body = {
        "value": [
            {"name": "Status", "displayName": "Status", "choice": {"choices": ["Draft", "Approved"]}},
            {"name": "Title", "displayName": "Title", "text": {}},
        ]
    }
    with respx.mock:
        _mock_token()
        respx.get(SITE_URL_MOCK).mock(return_value=httpx.Response(200, json={"id": SITE_ID}))
        route = respx.get(COLUMNS_URL_MOCK).mock(return_value=httpx.Response(200, json=body))
        columns = await _call(SITE_ID, LIST_ID)

    assert route.calls.last.request.url.path == f"/v1.0{COLUMNS_PATH}"
    assert columns == [
        {
            "internal_name": "Status",
            "display_name": "Status",
            "field_type": "choice",
            "allowed_values": ["Draft", "Approved"],
            "term_set_id": None,
        },
        {
            "internal_name": "Title",
            "display_name": "Title",
            "field_type": "text",
            "allowed_values": None,
            "term_set_id": None,
        },
    ]


@pytest.mark.unit
async def test_hidden_and_read_only_columns_are_filtered_out() -> None:
    body = {
        "value": [
            {"name": "Status", "displayName": "Status", "choice": {"choices": ["Draft"]}},
            {"name": "_Hidden", "displayName": "Hidden", "text": {}, "hidden": True},
            {"name": "Created", "displayName": "Created", "dateTime": {}, "readOnly": True},
        ]
    }
    with respx.mock:
        _mock_token()
        respx.get(SITE_URL_MOCK).mock(return_value=httpx.Response(200, json={"id": SITE_ID}))
        respx.get(COLUMNS_URL_MOCK).mock(return_value=httpx.Response(200, json=body))
        columns = await _call(SITE_ID, LIST_ID)

    assert [column["internal_name"] for column in columns] == ["Status"]


@pytest.mark.unit
async def test_resolves_library_by_display_name() -> None:
    body = {"value": [{"name": "Title", "displayName": "Title", "text": {}}]}
    with respx.mock:
        _mock_token()
        respx.get(SITE_URL_MOCK).mock(return_value=httpx.Response(200, json={"id": SITE_ID}))
        lists_route = respx.get(LISTS_URL_MOCK).mock(
            return_value=httpx.Response(200, json={"value": [{"id": LIST_ID}]})
        )
        respx.get(COLUMNS_URL_MOCK).mock(return_value=httpx.Response(200, json=body))
        columns = await _call(SITE_ID, LIBRARY_NAME)

    assert lists_route.called
    assert [column["internal_name"] for column in columns] == ["Title"]


@pytest.mark.unit
async def test_unknown_library_surfaces_as_clean_tool_error() -> None:
    with respx.mock:
        _mock_token()
        respx.get(SITE_URL_MOCK).mock(return_value=httpx.Response(200, json={"id": SITE_ID}))
        respx.get(LISTS_URL_MOCK).mock(return_value=httpx.Response(200, json={"value": []}))
        with pytest.raises(ToolError) as exc_info:
            await _call(SITE_ID, "No Such Library")

    message = str(exc_info.value)
    assert "No Such Library" in message
    assert "Traceback" not in message
    assert "NotFoundError" not in message
