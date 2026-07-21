"""Thin client over the Microsoft Graph REST API v1.0 (ADR-0004).

The shared substrate every tool (#10-#13) calls. Each public method maps onto exactly one
Graph request from the spec's **Graph API surface** table (a library *name* costs one extra
resolution request), authorized with a bearer token from the auth layer (:mod:`spdoc_mcp.auth`).
Non-2xx responses are translated into domain exceptions carrying the Graph error body — a 404
into :class:`~spdoc_mcp.errors.NotFoundError`, anything else into
:class:`~spdoc_mcp.errors.GraphError` — never a raw stack trace
(see ``.claude/standards/error-handling.md``).

Stateless: the only runtime state is the token cache the auth layer owns. Like that layer, a
single process-wide instance is obtained via :func:`get_graph_client`, so the stdio and HTTP/SSE
transports share one client.
"""

import functools
import re
from typing import Any, NoReturn, cast
from urllib.parse import urlparse

import httpx

from spdoc_mcp.auth import TokenProvider, get_token_provider
from spdoc_mcp.errors import GraphError, NotFoundError

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_REQUEST_TIMEOUT_SECONDS = 30
# Standard 8-4-4-4-12 hex GUID: a bare list ID needs no resolution request, unlike a library name.
_GUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class GraphClient:
    """Issues the five Graph operations and translates their failures into domain errors.

    Constructed without credentials; the token is fetched from the auth layer per request and
    never stored here. Obtain the shared instance via :func:`get_graph_client`.
    """

    def __init__(self, *, token_provider: TokenProvider | None = None) -> None:
        self._token_provider = token_provider or get_token_provider()

    async def resolve_site(self, site: str) -> dict[str, Any]:
        """Resolve a site URL or Graph site ID to the site resource (which carries its ``id``).

        A URL becomes Graph's ``{hostname}:/{server-relative-path}`` addressing; anything else is
        treated as a site ID and addressed directly.
        """
        return await self._request("GET", f"sites/{self._site_path(site)}")

    async def resolve_list(self, site_id: str, library: str) -> str:
        """Resolve a library name or list ID to the list ID.

        A GUID is already a list ID and passes through with no request. A name is matched on its
        ``displayName`` (the human-facing library name a user supplies).
        """
        if _GUID_PATTERN.match(library):
            return library
        data = await self._request(
            "GET",
            f"sites/{site_id}/lists",
            params={"$filter": f"displayName eq '{library}'"},
        )
        values = data.get("value", [])
        if not values:
            raise NotFoundError(f"No document library named {library!r} found in site {site_id!r}")
        return cast("str", values[0]["id"])

    async def list_documents(self, site_id: str, list_id: str) -> dict[str, Any]:
        """List items in a library, expanding each item's metadata ``fields``."""
        return await self._request(
            "GET",
            f"sites/{site_id}/lists/{list_id}/items",
            params={"$expand": "fields"},
        )

    async def get_document_metadata(self, site_id: str, list_id: str, item_id: str) -> dict[str, Any]:
        """Return all metadata field values for a single list item."""
        return await self._request("GET", f"sites/{site_id}/lists/{list_id}/items/{item_id}/fields")

    async def update_document_metadata(
        self, site_id: str, list_id: str, item_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Set one or more metadata field values on a list item; returns the updated fields."""
        return await self._request(
            "PATCH",
            f"sites/{site_id}/lists/{list_id}/items/{item_id}/fields",
            json=fields,
        )

    async def list_columns(self, site_id: str, list_id: str) -> dict[str, Any]:
        """Enumerate the metadata columns defined on a library."""
        return await self._request("GET", f"sites/{site_id}/lists/{list_id}/columns")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue one authorized Graph request and return its parsed JSON, or raise on non-2xx.

        The single choke point: every operation flows through here so authorization and error
        translation live in exactly one place.
        """
        token = await self._token_provider.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{GRAPH_BASE_URL}/{path}"
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.request(method, url, headers=headers, params=params, json=json)
        if response.is_success:
            return cast("dict[str, Any]", response.json())
        self._raise_graph_error(response)

    @staticmethod
    def _site_path(site: str) -> str:
        """Turn a site URL into Graph's ``{hostname}:/{path}`` form; pass a site ID through as-is."""
        parsed = urlparse(site)
        if parsed.scheme in ("http", "https"):
            return f"{parsed.netloc}:{parsed.path}"
        return site

    @staticmethod
    def _raise_graph_error(response: httpx.Response) -> NoReturn:
        """Translate a non-2xx response into a domain error, preserving the Graph error body.

        A 404 becomes :class:`NotFoundError`; any other status becomes :class:`GraphError`. A
        missing or non-JSON error body degrades to a status-only message rather than crashing.
        """
        body = _safe_json(response)
        error = GraphError.from_response(response.status_code, body)
        if response.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError(str(error))
        raise error


def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
    """Best-effort parse of a response body as a JSON object; ``None`` if it is neither."""
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@functools.lru_cache
def get_graph_client() -> GraphClient:
    """Return the process-wide :class:`GraphClient` singleton, shared across transports."""
    return GraphClient()
