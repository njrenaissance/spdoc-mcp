# spec — SharePoint Document Library Metadata MCP Server

Status: draft

## Purpose

Expose SharePoint document library metadata operations (read, list, update) as MCP tools, so that an LLM agent (Claude or otherwise) can discover, inspect, and modify metadata columns on documents stored in SharePoint Online — without the calling agent needing to know Microsoft Graph internals.

## Problem

The existing Microsoft 365 MCP connector provides read-only SharePoint access (`sharepoint_search`, `sharepoint_folder_search`, `read_resource`). There is no tool — first-party or in the public MCP registry — that allows an agent to **write** to a document's metadata columns (list item fields). This server fills that gap.

## Inputs / Outputs

### Input (per tool call)
- **Site identifier:** SharePoint site URL or Graph site ID.
- **Library identifier:** document library name or Graph list ID.
- **Document identifier:** filename, item ID, or search query — depending on the tool.
- **Field values (write calls):** a JSON object mapping column internal names to new values.

### Output (per tool call)
- **JSON** returned directly to the calling agent. Shape varies by tool (document list, field values, confirmation + updated fields). No file output.

## What we produce

An **MCP server** built with **FastMCP (Python)** that exposes a small set of tools over the Model Context Protocol. It runs as a local process (stdio transport) or a remote HTTP/SSE endpoint, depending on deployment context. (Framework choice: [ADR-0003](adr/0003-fastmcp-framework.md).)

## Where we persist

**Stateless.** The server holds no persistent data — no file, no database. Its only runtime state is an **in-memory OAuth token cache** (acquire / cache / refresh on expiry), owned by the auth layer. Every tool call reads from and writes to SharePoint via Graph; nothing is retained between calls.

## Method

**N/A — deterministic API integration.** This is not a rules / classical-ML / LLM classification problem: each tool maps directly onto a Microsoft Graph REST call. There is no inference step in the server itself; the reasoning lives in the calling agent.

## Tools

### `list_documents`
List or search documents in a library. Returns item IDs, filenames, and current metadata values. Supports optional filtering by column value or filename pattern.

### `get_document_metadata`
Return all metadata column values for a single document, identified by item ID or filename. Includes both display names and internal names for each column.

### `update_document_metadata`
Set one or more metadata column values on a single document. Accepts a JSON object of `{ internalColumnName: value }` pairs. Returns the updated field values as confirmation.

### `list_columns`
Enumerate the metadata columns defined on a document library — internal name, display name, field type, and (for choice/taxonomy columns) the allowed values. This is the discovery tool: it lets the agent know what's settable before attempting an update.

## Auth

**App-only client credentials — no interactive OAuth, no SSO, no user-facing auth flow.** The server authenticates as an application, not as a user. Setup is a one-time Entra ID app registration; at runtime the server reads three environment variables and acquires tokens silently. There is no browser redirect, no consent popup, and no user involvement at any point. (Auth model: [ADR-0001](adr/0001-app-only-client-credentials-auth.md).)

### Why a write-capable permission is required

This server does not modify file content, move or delete documents, or change permissions. However, updating a metadata column value on a document is a **write to a list item** in Graph terms (`PATCH .../items/{id}/fields`). Graph does not offer a narrower "metadata-only write" scope — any list item field update requires a permission that allows writes. Read-only permissions (`Sites.Read.All`) are insufficient.

### Required Graph permissions (application)
- `Sites.Selected` (preferred — scoped to only the specific sites this server needs to access) **or** `Sites.ReadWrite.All` (broader, simpler initial setup). The server reads documents and columns (read) and sets metadata column values (write). It does not use any other write capability the permission grants — file upload, deletion, and permission management are not exposed as tools. (Permission scope: [ADR-0002](adr/0002-permission-scope-sites-selected.md).)

### Credentials
Three environment variables, set once:

| Variable | Value |
|---|---|
| `AZURE_TENANT_ID` | Directory (tenant) ID from the Entra ID app registration |
| `AZURE_CLIENT_ID` | Application (client) ID |
| `AZURE_CLIENT_SECRET` | Client secret value |

These are never hardcoded, never passed as tool parameters, and never visible to the calling agent. The server manages its own token lifecycle (acquire, cache, refresh) at startup and on expiry.

## Graph API surface

All operations go through the Microsoft Graph REST API v1.0. (API choice: [ADR-0004](adr/0004-microsoft-graph-api.md).)

| Operation | Graph endpoint |
|---|---|
| List documents | `GET /sites/{site-id}/lists/{list-id}/items?expand=fields` |
| Get metadata | `GET /sites/{site-id}/lists/{list-id}/items/{item-id}/fields` |
| Update metadata | `PATCH /sites/{site-id}/lists/{list-id}/items/{item-id}/fields` |
| List columns | `GET /sites/{site-id}/lists/{list-id}/columns` |
| Resolve site | `GET /sites/{hostname}:/{site-path}` |

## Constraints / Rules

- **Metadata only:** this server reads documents/columns and writes list item field values (metadata tagging). It does not upload, download, move, or delete document files, and it does not modify permissions or sharing settings. The write surface is strictly limited to column values on existing documents.
- **Single-item writes:** `update_document_metadata` operates on one document per call. Bulk updates are achieved by the agent issuing multiple calls, not by a batch endpoint.
- **Internal names required for writes:** the `update_document_metadata` tool accepts column internal names, not display names. The `list_columns` tool exists so the agent (or user) can discover the mapping.
- **Field type awareness:** choice columns accept a string value from the allowed set. Managed metadata (taxonomy) columns require a `TermGuid`. Lookup columns require the lookup item ID. The `list_columns` output includes type information so the agent can construct valid payloads.
- **No schema mutation:** the server cannot add, remove, or rename columns on a library. It operates within the existing schema.

## Scale

Target: **interactive, single-document operations** — an agent updating a handful of documents per conversation. No batching, pagination beyond what Graph returns by default (200 items), or high-throughput requirements in v1. Graph rate limits (roughly 10,000 requests per 10 minutes per app) are not a concern at this scale.

## Deployment

Two transport modes, both supported by FastMCP (dual-transport decision: [ADR-0005](adr/0005-dual-transport-stdio-http.md)):

- **stdio** — for local use with Claude Desktop or Claude Code. The MCP server runs as a subprocess; config goes in `claude_desktop_config.json` or `.mcp.json`.
- **HTTP/SSE** — for remote or shared use. The server runs as a long-lived process behind a URL.

## Deferred implementation specifics

These details are deliberately left to per-issue implementation planning:

- Exact Entra ID app registration steps (tenant-specific).
- Whether to start with `Sites.ReadWrite.All` for simplicity and tighten to `Sites.Selected` later.
- Managed metadata (taxonomy) column write support — may require the SharePoint REST API rather than Graph for term resolution.
- Pagination strategy for libraries with > 200 documents.
- Support for multi-value columns (multi-choice, multi-lookup).

## Acceptance criteria

Product-level, observable behaviors that define done.

- Given a valid site and library, `list_columns` returns every user-defined metadata column with its internal name, display name, and type.
- Given a valid site, library, and document, `get_document_metadata` returns the current values of all metadata columns for that document.
- Given a valid column internal name and a valid value, `update_document_metadata` writes the value and the subsequent `get_document_metadata` call reflects the change.
- A choice column rejects a value not in its allowed set (Graph returns an error; the server surfaces it cleanly, not as a raw stack trace).
- `list_documents` returns documents with their item IDs and current metadata, sufficient for the agent to identify a target document and call `update_document_metadata`.
- Auth credentials are read from environment variables; the server never prompts for credentials or accepts them as tool parameters.
- The server starts and responds to tool calls over both stdio and HTTP/SSE transports.
- (Criteria refined as issues are planned; each issue decomposes the relevant ones into concrete tests.)
