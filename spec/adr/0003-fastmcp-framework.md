# ADR-0003: FastMCP as the MCP framework

| Field | Value |
|---|---|
| Status | proposed |

## Context

The server exposes a small, fixed set of tools (`list_documents`,
`get_document_metadata`, `update_document_metadata`, `list_columns`) over the Model Context
Protocol, and must support both stdio and HTTP/SSE transports (see
[ADR-0005](0005-dual-transport-stdio-http.md)). We need a Python framework to define those
tools, validate their inputs, and serve them over MCP.

## Decision

Build the server with **FastMCP (Python)**. Tools are declared as decorated Python functions
with typed signatures; FastMCP handles MCP protocol wiring, schema generation from type
hints, and both transports out of the box.

## Alternatives

- **Raw `mcp` Python SDK.** Rejected: requires hand-wiring server setup, tool registration,
  and JSON-schema declarations for the same four tools — more boilerplate for no additional
  capability at this scale.
- **Hand-rolled MCP server.** Rejected: re-implements protocol framing and transport
  handling that FastMCP already provides and maintains; pure cost, no benefit.

## Tradeoffs

- **Gain:** minimal boilerplate; type-hint-driven tool schemas; built-in stdio and HTTP/SSE
  transports; idiomatic Python that maps cleanly onto the clean-code and typing standards.
- **Give up:** a framework dependency and its release cadence; some indirection between the
  tool function and the underlying protocol, which the team must understand when debugging.

## Consequences

- Adds `fastmcp` (and its transitive MCP dependencies) to `pyproject.toml` at inner Build
  time — greenfield; no dependency exists yet.
- Tool input validation leans on FastMCP's type-hint schema generation, complementing
  `pydantic`-based settings validation from the configuration standard.
- This decision is referenced from the **What we produce** section of `spec/spec.md`.
