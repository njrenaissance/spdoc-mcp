# Issue #8 — Microsoft Graph API client wrapper — Implementation Plan

> **Phase:** inner **Plan** (HITL gate) per [`coding-workflow.md`](../../coding-workflow.md).
> This document + the committed TDD tests in [`tests/test_graph.py`](../../tests/test_graph.py)
> are the deliverable of this phase. No production code (`src/spdoc_mcp/graph.py`) is written
> yet — inner **Build** writes it to satisfy the committed tests. The branch is therefore
> **red by design** until Build lands (see "Definition of done / red-by-design" below).

Issue: [#8 — Microsoft Graph API client wrapper](https://github.com/njrenaissance/spdoc-mcp/issues/8)
Sequencing: Group 3; depends on #6 (errors) + #7 (auth), which are merged; blocks the four
tool issues (#10–#13); on the critical path.

---

## Purpose

A thin, stateless client over the **Microsoft Graph REST API v1.0** ([ADR-0004](../adr/0004-microsoft-graph-api.md))
that every tool calls. It obtains a bearer token from the auth layer ([#7](../../src/spdoc_mcp/auth.py)),
issues the five Graph requests the spec's **Graph API surface** table enumerates, returns parsed
JSON, and translates any non-2xx response into a domain exception carrying the Graph error detail —
never a raw stack trace ([`error-handling.md`](../../.claude/standards/error-handling.md)).

## Inputs / Outputs (contract)

| Method | Input | Graph request | Output |
|---|---|---|---|
| `resolve_site(site)` | site URL **or** Graph site ID | `GET /sites/{hostname}:/{site-path}` (URL) or `GET /sites/{site-id}` (ID) | site JSON (has `id`) |
| `resolve_list(site_id, library)` | resolved site ID + library name **or** list ID | GUID → no request; name → `GET /sites/{site-id}/lists?$filter=displayName eq '<name>'` | list ID (`str`) |
| `list_documents(site_id, list_id)` | resolved IDs | `GET /sites/{site-id}/lists/{list-id}/items?$expand=fields` | items JSON |
| `get_document_metadata(site_id, list_id, item_id)` | resolved IDs + item ID | `GET /sites/{site-id}/lists/{list-id}/items/{item-id}/fields` | field values JSON |
| `update_document_metadata(site_id, list_id, item_id, fields)` | resolved IDs + item ID + `{internalName: value}` | `PATCH …/items/{item-id}/fields` (body = `fields`) | updated field values JSON |
| `list_columns(site_id, list_id)` | resolved IDs | `GET /sites/{site-id}/lists/{list-id}/columns` | columns JSON |

- **What we produce:** a library module (one class), consumed by the tool layer. Not a CLI/service on its own.
- **Where we persist:** stateless — the only runtime state is the auth layer's in-memory token cache, owned by #7.
- **Method:** deterministic API integration — no inference. Each method is one Graph call (plus one resolution call for a library *name*).

## Module design (`src/spdoc_mcp/graph.py`)

Mirrors the shape #7 already established (`TokenProvider` + cached `get_token_provider()`):

```python
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

class GraphClient:
    def __init__(self, *, token_provider: TokenProvider | None = None) -> None: ...
        # keyword-only; defaults to get_token_provider(); credentials never pass through here

    async def resolve_site(self, site: str) -> dict[str, Any]: ...
    async def resolve_list(self, site_id: str, library: str) -> str: ...
    async def list_documents(self, site_id: str, list_id: str) -> dict[str, Any]: ...
    async def get_document_metadata(self, site_id: str, list_id: str, item_id: str) -> dict[str, Any]: ...
    async def update_document_metadata(self, site_id: str, list_id: str, item_id: str,
                                       fields: dict[str, Any]) -> dict[str, Any]: ...
    async def list_columns(self, site_id: str, list_id: str) -> dict[str, Any]: ...

@functools.lru_cache
def get_graph_client() -> GraphClient: ...        # process-wide singleton, shared across transports
```

**Internals (all private, not part of the tested contract):**

- `_request(method, path, *, params=None, json=None) -> dict[str, Any]` — the single choke point:
  1. `token = await self._token_provider.get_token()`
  2. open a short-lived `httpx.AsyncClient(base_url=GRAPH_BASE_URL, timeout=…)` — same per-request
     client pattern as `auth.py` (fine at v1's interactive, single-document scale; a persistent
     client is a later optimization, not needed now).
  3. send with header `Authorization: Bearer {token}`.
  4. on 2xx → return parsed JSON body; otherwise → `_raise_graph_error(response)`.
- `_site_path(site)` — a URL (`startswith http`) → `"{netloc}:{path}"` (Graph's `{hostname}:/{site-path}`
  colon addressing); anything else is treated as a Graph site ID and passed through unchanged.
- `_is_guid(value)` — recognises a bare list-ID GUID so `resolve_list` can short-circuit with no request.
- `_raise_graph_error(response)` — best-effort JSON parse of the error body; **404 → `NotFoundError`**
  (detail in the message, its shape from #6), **any other non-2xx → `GraphError.from_response(status, body)`**
  (structured `status_code` / `graph_code` / `graph_message`). A non-JSON body degrades to status-only,
  never crashes.

### Error mapping

| Graph response | Raised | Carries |
|---|---|---|
| `404` | `NotFoundError` | status + Graph `code`/`message` in the message |
| any other non-2xx (`400`, `401`, `403`, `429`, `5xx`, …) | `GraphError` | `status_code`, `graph_code`, `graph_message` (via `GraphError.from_response`) |
| non-JSON / empty error body | `GraphError` | `status_code` only; `graph_code`/`graph_message` = `None` (graceful) |

Both exception types already exist from #6; this issue adds **no** new exception types.

## Acceptance criteria → tests

Every criterion in issue #8 maps to committed tests in `tests/test_graph.py`:

| Issue #8 acceptance criterion | Test(s) |
|---|---|
| Each of the five operations issues the exact Graph v1.0 request and returns parsed JSON | `test_resolve_site_from_url_…`, `test_resolve_site_from_site_id_…`, `test_resolve_list_resolves_name_…`, `test_list_documents_expands_fields`, `test_get_document_metadata_…`, `test_update_document_metadata_…`, `test_list_columns_…` |
| Site accepts a URL **or** a Graph site ID; library accepts a name **or** a list ID | `test_resolve_site_from_url_…`, `test_resolve_site_from_site_id_…`, `test_resolve_list_returns_guid_without_a_request`, `test_resolve_list_resolves_name_via_displayname_filter` |
| A non-2xx response raises `GraphError`/`NotFoundError` carrying status + Graph detail, never a stack trace | `test_bad_request_raises_graph_error_…`, `test_not_found_response_raises_not_found_error_…`, `test_non_json_error_body_degrades_…` |
| All requests are authorized via a token from the auth layer | `test_requests_carry_bearer_token_from_auth_layer` (+ every test drives the real `TokenProvider`) |
| Library name with no match resolves cleanly (implied by "library accepts a name") | `test_resolve_list_raises_not_found_when_name_has_no_match` |
| Client is a shared singleton, like the token provider (established pattern) | `test_get_graph_client_is_cached`, `test_graph_base_url_is_v1` |

**Test approach** (per [`testing.md`](../../.claude/standards/testing.md)): only the network boundary is
faked, via `respx`; the `TokenProvider` is a **real** constructed collaborator (never mocked), with a
frozen clock — so each test also proves the request was authorized end-to-end. This is the same fixture
shape as `tests/test_auth.py`.

## Open decisions for the HITL gate

Two choices the tests deliberately pin — the gate is where to overrule them, and the tests change with the decision:

1. **OData `$` prefix.** The spec's endpoint table writes `?expand=fields` (and implies filtering by
   name), but Graph v1.0 **requires** the `$` prefix on OData system query options (`$expand`, `$filter`).
   The plan uses `$expand=fields` / `$filter=displayName eq '…'` — the form that actually works against
   Graph. Reading the spec table's `expand` as shorthand for `$expand`. **Recommend: keep `$`.**
2. **Library-name resolution strategy.** A library *name* is resolved via
   `$filter=displayName eq '<name>'`, taking the first match; a GUID passes straight through with no
   request. Alternative considered — addressing `…/lists/{name}` directly — is rejected because that
   path segment addresses a list by its URL `name`, not its human-facing `displayName`, which is what a
   user supplies. **Recommend: filter on `displayName`.**

## Out of scope (deferred per spec / issue #8)

- MCP tool definitions / registration (#10–#13).
- Managed-metadata (taxonomy) term (`TermGuid`) resolution — may need the SharePoint REST API ([ADR-0004](../adr/0004-microsoft-graph-api.md) consequences).
- Pagination beyond Graph's default (~200 items) and multi-value columns.
- Retry/back-off on `429`/`5xx` — surfaced as `GraphError`; a retry policy is a later concern.

## Definition of done / red-by-design

Per `coding-workflow.md`, inner Plan commits the plan + agreed tests **before** any production code, so:

- **Now (Plan):** `tests/test_graph.py` is committed and **fails** — `uv run pytest` errors at collection
  because `spdoc_mcp.graph` does not exist yet. This is expected; the draft PR is intentionally red.
- **Next (Build):** implement `src/spdoc_mcp/graph.py` exactly to the contract above until
  `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` all pass. No test should need changing
  unless the human revised an "Open decision" at this gate.

Supporting tooling change included now: `pyproject.toml` pins `known-first-party = ["spdoc_mcp"]` for
ruff's import sorter, so import grouping doesn't flip based on whether a submodule exists on disk yet
(a TDD test importing a not-yet-written module would otherwise be grouped as third-party).
