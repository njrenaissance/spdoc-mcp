---
type: Quickstart
title: spdoc-mcp Quickstart
description: SharePoint document library metadata MCP server — get started, understand the architecture, and find task-oriented guidance.
---

# spdoc-mcp Quickstart

**spdoc-mcp** is a Model Context Protocol (MCP) server that exposes SharePoint document library metadata operations — read, list, update — as MCP tools. It allows an LLM agent (Claude or otherwise) to discover, inspect, and modify metadata columns on documents stored in SharePoint Online, without needing to know Microsoft Graph internals.

This wiki documents the architecture, setup, and development workflow. Start here, then follow the links to dive deeper into specific areas.

## What is this?

An **MCP server** built with **FastMCP (Python)** that runs as a local subprocess (stdio) or a remote HTTP/SSE endpoint. It authenticates to Microsoft Graph as an application (app-only OAuth 2.0 client credentials), then exposes these tools:

- **`list_documents`** — list or search documents in a SharePoint library
- **`get_document_metadata`** — retrieve all metadata column values for a single document
- **`update_document_metadata`** — set one or more metadata column values on a document
- **`list_columns`** — enumerate the metadata columns defined on a library (discovery tool)

See [spec/spec.md](/spec/spec.md) for the complete specification, acceptance criteria, and API surface.

## Architecture

The server is structured for **testability, configuration clarity, and clean error handling**.

- **[Architecture Overview](./architecture/overview.md)** — module structure, auth lifecycle, error boundary, transport setup, and dependency flow.
- **[Authentication & OAuth](./architecture/auth.md)** — app-only client-credentials flow, token caching, refresh logic, and credential security.

## Setup & Run

### Prerequisites

- Python 3.13+
- `uv` package manager (https://docs.astral.sh/uv/)
- Azure Entra ID app registration with client credentials (see setup guide below)

### Install

```bash
uv sync
```

### Environment

Create a `.env` file with your Azure app credentials:

```bash
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=your-client-id
AZURE_CLIENT_SECRET=your-client-secret
```

See [.env.example](.env.example) for all available variables (transport mode, host, port).

**Security note:** credentials are read from environment variables or `.env` only — never hardcoded, never passed as tool parameters, never logged. See [Architecture: Authentication](./architecture/auth.md) for details.

### Run as stdio server (Claude Desktop / Claude Code)

```bash
uv run spdoc-mcp
# or equivalently:
uv run python -m spdoc_mcp
```

Register in your MCP client config (`.mcp.json` for Claude Code, `claude_desktop_config.json` for Claude Desktop):

```json
{
  "mcpServers": {
    "spdoc-mcp": {
      "command": "uv",
      "args": ["run", "spdoc-mcp"],
      "cwd": "/path/to/spdoc-mcp",
      "env": {
        "AZURE_TENANT_ID": "your-tenant-id",
        "AZURE_CLIENT_ID": "your-client-id",
        "AZURE_CLIENT_SECRET": "your-client-secret"
      }
    }
  }
}
```

Logs are written to stderr; stdout carries the MCP protocol stream and must stay clean.

### Run as HTTP server (future use)

```bash
SPDOC__TRANSPORT_MODE=http SPDOC__TRANSPORT_PORT=8000 uv run spdoc-mcp
```

See [settings.py](/src/spdoc_mcp/settings.py) for all transport configuration options.

## Testing

Run the full test suite:

```bash
uv run pytest
```

View coverage:

```bash
uv run pytest --cov
```

The project enforces a 70% coverage floor. See [Testing Guide](./testing.md) for patterns and conventions.

## Linting & Format

```bash
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy src         # type check
```

## Key Concepts

### Stateless Design

The server holds **no persistent data**. Its only runtime state is an **in-memory OAuth token cache** (acquire / refresh on expiry). Every tool call reads from and writes to SharePoint via Microsoft Graph; nothing is retained between calls.

See [spec: Where we persist](/spec/spec.md#where-we-persist) and [ADR-0001](/spec/adr/0001-app-only-client-credentials-auth.md).

### Authentication Model

**App-only OAuth 2.0 client credentials** — the server authenticates as an application, not as a user. Setup is a one-time Entra ID app registration; at runtime the server reads three environment variables and acquires tokens silently. There is no browser redirect, no consent popup, and no user involvement at any point.

See [Architecture: Authentication](./architecture/auth.md) and [ADR-0001](/spec/adr/0001-app-only-client-credentials-auth.md).

### Error Handling

The server catches domain errors **once, at the system boundary** (tool call), logs the full exception, and converts it into a clean MCP response. Programmer errors are masked to the caller.

Domain errors are defined in [errors.py](/src/spdoc_mcp/errors.py):
- `ConfigError` — missing or invalid configuration
- `AuthError` — OAuth token acquisition failed
- `NotFoundError` — site, library, document, or column could not be resolved
- `ToolArgumentError` — bad tool arguments
- `GraphError` — non-2xx response from Microsoft Graph (carries status + error code/message)

See [Middleware & Error Handling](./architecture/overview.md#error-handling-middleware) and `.claude/standards/error-handling.md`.

### Architectural Decisions

Key design rationales are documented in [Specification & Architecture Decision Records](./reference/spec-and-adrs.md), including:

- **App-only OAuth** — why the server authenticates as an application, not a user
- **FastMCP framework** — why we chose FastMCP for the MCP implementation
- **Dual transports** — stdio for local development, HTTP/SSE for remote deployment
- **Graph API** — why Microsoft Graph REST API v1.0 over SharePoint REST
- **Permission scope** — Sites.Selected vs. Sites.ReadWrite.All

See [Specification & Architecture Decision Records](./reference/spec-and-adrs.md) for full details and rationales.

## Next Steps

### For First-Time Setup

1. Register an Entra ID app and obtain credentials (see [setup guide](#setup--run)).
2. Create `.env` with your credentials.
3. Run `uv run spdoc-mcp` to test the server startup.
4. Try registering the server with Claude Desktop or Claude Code.

### For Development

1. Read [Architecture Overview](./architecture/overview.md) to understand the module layout.
2. Review [Authentication & OAuth](./architecture/auth.md) to understand token lifecycle.
3. Explore the test suite in `/tests` to understand testing patterns.
4. Implement one tool at a time using the tool-registration contract in [server.py](/src/spdoc_mcp/server.py).

### For Contributing

1. Ensure code follows Clean Code principles and the Gang of Four design patterns where applicable.
2. Run the full test suite (`uv run pytest`) before committing.
3. Run linting and type check (`uv run ruff check .` and `uv run mypy src`).
4. Regenerate the wiki: `openwiki code --update` (see `.claude/standards/wiki.md`).

## Backlog

- **MCP Tools Implementation** — Define and implement the four tools (`list_documents`, `get_document_metadata`, `update_document_metadata`, `list_columns`). See `/spec/spec.md` for tool spec and acceptance criteria.
- **HTTP/SSE Transport** — Dual transport is designed in (ADR-0005, settings), but HTTP/SSE deployment is not yet implemented. Low priority for v1 (stdio is primary).
- **Pagination** — Current implementation expects Graph to return up to 200 items by default. Explicit pagination strategy is deferred.
- **Managed Metadata (Taxonomy) Columns** — May require the SharePoint REST API rather than Graph for term resolution. Deferred pending tool implementation and real-world usage.
- **Multi-Value Columns** — Support for multi-choice and multi-lookup columns is deferred.

---

**Generated by OpenWiki.** Do not hand-edit; regenerate with `openwiki code --update` after code changes.
