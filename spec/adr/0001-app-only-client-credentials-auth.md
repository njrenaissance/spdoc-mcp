# ADR-0001: App-only client-credentials authentication

| Field | Value |
|---|---|
| Status | accepted |

## Context

The server must call Microsoft Graph to read and write SharePoint document-library
metadata. It runs unattended — invoked by an LLM agent over MCP (stdio subprocess or a
long-lived HTTP/SSE process), with no human present at the moment a tool executes. Graph
supports several ways to obtain a token, and the choice determines whether a user must be
in the loop, what consent is required, and how the token lifecycle is managed.

## Decision

Authenticate as an **application** using the OAuth 2.0 **client-credentials flow**
(app-only): a one-time Entra ID app registration, and at runtime three environment
variables (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`) from which the
server silently acquires, caches, and refreshes tokens. No interactive OAuth, no SSO, no
browser redirect, no consent popup, no user involvement at call time.

## Alternatives

- **Delegated OAuth (authorization-code flow, user context).** Rejected: requires an
  interactive sign-in and a present user to consent and hold a session — incompatible with
  an unattended agent-driven server. Would also scope access to the invoking user's
  permissions, which the calling agent has no identity for.
- **Device-code flow.** Rejected: still needs a human to complete a one-time interactive
  step and to re-authenticate on refresh-token expiry; unsuitable for a headless process.
- **Managed identity (Azure-hosted).** Rejected for v1: ties the server to an Azure hosting
  context and does not cover the local stdio deployment (Claude Desktop / Claude Code) that
  is explicitly in scope.

## Tradeoffs

- **Gain:** fully unattended operation; deterministic startup; one setup step; the same
  auth model works identically for stdio and HTTP/SSE transports.
- **Give up:** no per-user identity or audit trail — every action is attributed to the
  application principal, not the end user. A client secret must be managed and rotated
  (see Consequences). Access is governed by the app's own Graph permissions, not a user's.

## Consequences

- The server owns its token lifecycle (acquire / cache in memory / refresh on expiry); this
  is the only server-side state (see ADR-0005 and the spec's "Where we persist").
- The client secret is a required, no-default secret handled per the configuration standard
  (`SecretStr`, environment-sourced, never a tool parameter, never logged).
- The required Graph *permission scope* is a separate decision — see
  [ADR-0002](0002-permission-scope-sites-selected.md).
- This decision is referenced from the **Auth** section of `spec/spec.md`.
