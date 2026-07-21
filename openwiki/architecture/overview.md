---
type: Architecture
title: Architecture Overview
description: Module structure, auth lifecycle, error handling, transport setup, and dependency flow for the spdoc-mcp server.
---

# Architecture Overview

The spdoc-mcp server is organized around **clean module boundaries, app-only authentication, and stateless tool execution**. This page explains the architecture at a high level; dive into [Authentication & OAuth](./auth.md) for credential and token details.

## Module Structure

```
src/spdoc_mcp/
├── __init__.py           # Package marker
├── __main__.py           # Enable `python -m spdoc_mcp` alias
├── server.py             # FastMCP app factory, tool registration entrypoint
├── auth.py               # OAuth token acquisition, cache, refresh
├── settings.py           # Configuration (Pydantic) — single source of truth
├── middleware.py         # Error-handling boundary
├── errors.py             # Domain exception hierarchy
└── logging_config.py     # Logging setup (stderr, INFO level)
```

Each module has a clear responsibility and explicit internal-only state (if any).

## Startup & Entrypoint

The console script entrypoint is `spdoc-mcp` (defined in [pyproject.toml](/pyproject.toml)):

```python
# Entry in pyproject.toml [project.scripts]
spdoc-mcp = "spdoc_mcp.server:main"
```

`main()` in [server.py](/src/spdoc_mcp/server.py) does three things:

1. **Configure logging** — call `configure_logging()` to send logs to stderr at INFO level (required under stdio transport so stdout stays clean for the MCP protocol stream).
2. **Create the app** — call `create_app()` to build the FastMCP instance, attach error handling middleware, and register tools.
3. **Run the server** — call `app.run(transport="stdio")` to serve the MCP server over stdio.

```python
def main() -> None:
    """Console-script entrypoint: serve the MCP server over stdio."""
    configure_logging()
    create_app().run(transport="stdio", show_banner=False)
```

Transport selection (stdio vs. HTTP) lives here, so adding HTTP/SSE later is a change to `main()`, not a rewrite of the app or tools.

## FastMCP App Factory

`create_app()` in [server.py](/src/spdoc_mcp/server.py) builds the app:

```python
def create_app() -> FastMCP:
    """Build the FastMCP app with the boundary error handler and tools attached."""
    app: FastMCP = FastMCP(APP_NAME, mask_error_details=True)
    app.add_middleware(ErrorHandlingMiddleware())
    _register_tools(app)
    return app
```

- **`mask_error_details=True`** — prevents FastMCP from surfacing raw exception text for unhandled (programmer) errors; the middleware handles domain-level errors separately.
- **ErrorHandlingMiddleware** — catches domain errors at the tool boundary and converts them to clean MCP responses.
- **`_register_tools(app)`** — a seam where future tool implementations register themselves (empty for now).

## Tool Registration Contract

Tools register via a module-level `register(app: FastMCP) -> None` function. The [server.py](/src/spdoc_mcp/server.py) file documents this contract:

```python
def _register_tools(app: FastMCP) -> None:
    """Register every tool onto ``app``.

    The four tool issues (#10-#13) each add one ``register(app)`` call here.
    No tools exist yet, so this is intentionally empty.
    """
```

When a tool is implemented, a new module (e.g., `tools/list_documents.py`) exposes:

```python
async def register(app: FastMCP) -> None:
    @app.tool()
    async def list_documents(...) -> ...:
        """Tool implementation."""
```

Then `_register_tools()` calls `tools.list_documents.register(app)`.

This keeps tool declaration out of import time (no side effects, no circular imports) and gives each tool a single, explicit seam to plug into the app.

## Configuration Management

**Single source of truth:** all configuration lives in [settings.py](/src/spdoc_mcp/settings.py), using Pydantic `BaseSettings`. The module is the **only place** in the codebase that reads `os.environ` or `.env` files.

```python
# settings.py structure (simplified)
class AzureSettings(BaseSettings):
    """Azure app credentials (ADR-0001)."""
    tenant_id: SecretStr
    client_id: SecretStr
    client_secret: SecretStr

class TransportSettings(BaseSettings):
    """Transport/runtime config (ADR-0005)."""
    mode: Literal["stdio", "http"] = "stdio"
    host: str = "0.0.0.0"
    port: int = 8000

class Settings(BaseSettings):
    """Single source of truth."""
    azure: AzureSettings
    transport: TransportSettings

@functools.lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton."""
    return Settings()
```

Access config **only** via `get_settings()`. This:
- Makes secrets explicit (marked `SecretStr`, never logged).
- Makes config testable (override defaults in tests).
- Prevents scattered env-var reads across the codebase.

See `.claude/standards/configuration.md` for the full configuration standard.

## Authentication & Token Lifecycle

The server authenticates as an application using OAuth 2.0 client credentials (app-only).

**[Authentication & OAuth](./auth.md)** covers credential flow, token caching, and refresh logic in detail.

Key points:
- Credentials come from `AzureSettings` (tenant ID, client ID, secret).
- Tokens are cached **in memory** — this is the server's only runtime state.
- The `TokenProvider` singleton (in [auth.py](/src/spdoc_mcp/auth.py)) owns the cache and handles acquire/refresh.
- Tools call `await token_provider.get_token()` to get a bearer token string on each request.
- No token or secret is ever persisted to disk or logged.

## Error Handling & Middleware

The server follows a **single catch point at the system boundary** pattern:

1. A tool raises a domain exception (subclass of `AppError`).
2. FastMCP catches it and converts it to a `ToolError`.
3. **ErrorHandlingMiddleware** (in [middleware.py](/src/spdoc_mcp/middleware.py)) inspects the exception:
   - If the **cause** is a domain `AppError`, log it in full and re-surface it as a clean MCP error (message only, no trace).
   - Otherwise, it's a programmer error — leave it as FastMCP produced it (masked by `mask_error_details=True`).

```python
class ErrorHandlingMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except ToolError as err:
            cause = err.__cause__
            if isinstance(cause, AppError):
                logger.error("tool_call_failed", extra={"tool": context.message.name}, exc_info=cause)
                raise ToolError(str(cause)) from cause
            raise
```

This ensures:
- Caller sees a clean error message (from `str(AppError)`), not a raw stack trace.
- Full error details are logged server-side for debugging.
- No sensitive information (credentials, internal paths) leaks to the caller.

### Domain Exceptions

All server exceptions subclass `AppError` (in [errors.py](/src/spdoc_mcp/errors.py)):

- **`ConfigError`** — missing or invalid configuration (e.g., unset credential env var).
- **`AuthError`** — OAuth token acquisition or refresh failed.
- **`NotFoundError`** — a site, library, document, or column could not be resolved.
- **`ToolArgumentError`** — bad tool arguments (e.g., a choice value not in the column's allowed set).
- **`GraphError`** — non-2xx response from Microsoft Graph. Carries HTTP status and Graph error code/message so the caller can surface a clean, specific message.

When translating a lower-level error into a domain one, always chain it:

```python
raise ConfigError("missing AZURE_TENANT_ID") from original_err
```

Dropping the `from` clause breaks the exception chain and hides the real root cause.

## Logging

The server uses Python's standard library `logging` module (not structlog, per `.claude/standards/logging.md`).

- **Configured in** [logging_config.py](/src/spdoc_mcp/logging_config.py).
- **Default level:** `INFO`.
- **Output:** stderr (so stdout stays clean for the MCP protocol stream under stdio transport).
- **Format:** `%(asctime)s %(levelname)s %(name)s %(message)s`.

Call `configure_logging()` once at startup (in [server.py](/src/spdoc_mcp/server.py)):

```python
def configure_logging(level: int = logging.INFO) -> None:
    """Send application logs to stderr at ``level``."""
    # Idempotent: safe to call multiple times
    root = logging.getLogger()
    root.setLevel(level)
    if any(_is_stderr_handler(handler) for handler in root.handlers):
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
```

**No secrets in logs.** Credentials and tokens are never logged. `SecretStr` fields are automatically redacted by Pydantic.

## Data Flow

A typical tool call flows like this:

1. **MCP client** (Claude) calls a tool with parameters.
2. **FastMCP** deserializes the request, looks up the tool.
3. **Tool** executes:
   - Reads config via `get_settings()`.
   - Gets a token via `await token_provider.get_token()` (acquires or refreshes as needed).
   - Calls Microsoft Graph via httpx.
   - Parses the response, raises domain errors if needed.
4. **ErrorHandlingMiddleware** catches any exceptions:
   - Domain errors → log and convert to clean MCP error.
   - Programmer errors → mask and surface as generic error.
5. **MCP client** receives the result or error.

## Dependency Graph

```
server.py (create_app, main)
  ├── fastmcp (FastMCP framework)
  ├── middleware.py (ErrorHandlingMiddleware)
  │   └── errors.py (AppError hierarchy)
  ├── logging_config.py (configure_logging)
  └── [tools] (future tool modules)

[tools]
  ├── auth.py (TokenProvider, get_token)
  │   ├── settings.py (get_settings)
  │   └── errors.py (AuthError)
  ├── settings.py (get_settings)
  ├── errors.py (domain exceptions)
  └── httpx (Graph HTTP client)
```

No circular imports. Each module depends only on modules below it (or at the same level, e.g., tools → errors).

## Testing Strategy

Tests live in `/tests`:

- `test_settings.py` — configuration parsing and validation.
- `test_auth.py` — token acquisition, caching, refresh.
- `test_server.py` — app factory, middleware integration.
- `test_middleware.py` — error handling boundary.
- `test_errors.py` — exception behavior.
- `test_logging_config.py` — logging setup.
- `test_main.py` — entrypoint (minimal).

See [Testing Guide](../testing.md) for patterns and conventions.

---

**Generated by OpenWiki.** For authentication details, see [Authentication & OAuth](./auth.md).
