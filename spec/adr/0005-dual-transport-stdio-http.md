# ADR-0005: Support both stdio and HTTP/SSE transports

| Field | Value |
|---|---|
| Status | proposed |

## Context

An MCP server can be reached over different transports. This server has two distinct usage
contexts: a developer running it locally alongside Claude Desktop or Claude Code, and a
shared/remote deployment reachable by a URL. The transport choice affects how the process is
launched, configured, and where it runs.

## Decision

Support **both** transports, both provided by FastMCP
([ADR-0003](0003-fastmcp-framework.md)):

- **stdio** — the server runs as a subprocess; configured via `claude_desktop_config.json`
  or `.mcp.json`. For local use.
- **HTTP/SSE** — the server runs as a long-lived process behind a URL. For remote or shared
  use.

## Alternatives

- **stdio only.** Rejected: simplest, but excludes the remote/shared deployment that is in
  scope.
- **HTTP/SSE only.** Rejected: forces a hosted process even for a single developer using
  Claude Desktop locally, where a stdio subprocess is the natural, zero-infrastructure fit.

## Tradeoffs

- **Gain:** covers both the local-developer and shared-service contexts with one codebase;
  FastMCP absorbs most of the per-transport wiring, so the marginal cost is low.
- **Give up:** two transport paths to configure, document, and test — the "server starts and
  responds over both stdio and HTTP/SSE" acceptance criterion needs coverage on each, which
  is closer to an integration concern than a pure unit test.

## Consequences

- The server exposes a way to select the transport at startup (deferred to inner Build /
  inner Plan for the exact mechanism — flag, env var, or entrypoint).
- Because both are stateless request/response over the same tool implementations, the only
  runtime state remains the in-memory token cache
  ([ADR-0001](0001-app-only-client-credentials-auth.md)); transport choice adds no
  persistence.
- This decision is referenced from the **Deployment** section of `spec/spec.md`.
