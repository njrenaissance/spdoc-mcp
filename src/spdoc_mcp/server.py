"""FastMCP application factory and process entrypoint.

``create_app`` builds the :class:`~fastmcp.FastMCP` instance every tool issue
registers onto, attaches the system-boundary error handler
(:class:`~spdoc_mcp.middleware.ErrorHandlingMiddleware`), and wires in the tools.
``main`` is the console-script entrypoint (``spdoc-mcp``), running the server
over the stdio transport for local use with Claude Desktop / Claude Code.

**Tool-registration contract.** Each tool module exposes
``def register(app: FastMCP) -> None`` that declares its tool with ``@app.tool``;
``_register_tools`` calls each one. This keeps tool declaration out of import
time (no side effects, no circular imports) and gives the four tool issues a
single, explicit seam to plug into.

The app itself is transport-agnostic — ``create_app`` knows nothing about how it
is served. Transport selection lives only in ``main``, so adding the HTTP/SSE
transport later is a change here, not a rewrite of the app or its tools.
"""

from fastmcp import FastMCP

from spdoc_mcp.logging_config import configure_logging
from spdoc_mcp.middleware import ErrorHandlingMiddleware

APP_NAME = "spdoc-mcp"


def create_app() -> FastMCP:
    """Build the FastMCP app with the boundary error handler and tools attached.

    ``mask_error_details=True`` stops FastMCP surfacing raw exception text for
    unhandled (programmer) errors; :class:`ErrorHandlingMiddleware` re-surfaces
    the clean message for expected domain :class:`~spdoc_mcp.errors.AppError`s.
    """
    app: FastMCP = FastMCP(APP_NAME, mask_error_details=True)
    app.add_middleware(ErrorHandlingMiddleware())
    _register_tools(app)
    return app


def _register_tools(app: FastMCP) -> None:
    """Register every tool onto ``app``.

    The four tool issues (#10-#13) each add one ``register(app)`` call here.
    No tools exist yet, so this is intentionally empty.
    """


def main() -> None:
    """Console-script entrypoint: serve the MCP server over stdio."""
    configure_logging()
    create_app().run(transport="stdio", show_banner=False)
