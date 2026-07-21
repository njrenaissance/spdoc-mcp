"""The single system-boundary error catch for tool calls.

Per ``.claude/standards/error-handling.md`` the codebase catches broadly *once*,
at the system boundary, logs the full exception, and converts it into a clean
response there. For this MCP server that boundary is the tool call.

FastMCP runs a tool inside ``call_next`` and, if the tool raises, converts the
exception into a :class:`ToolError` before it reaches middleware — preserving the
original exception as ``__cause__``. This middleware inspects that cause:

* A domain :class:`AppError` is logged in full and re-surfaced as a
  :class:`ToolError` carrying *only* the domain message — no framework prefix,
  no stack trace.
* Anything else is a programmer error: it is left as FastMCP produced it. With
  ``mask_error_details=True`` on the app (see :mod:`spdoc_mcp.server`) that means
  a generic message, so internal detail never crosses the trust boundary to the
  calling agent.
"""

import logging

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp import types as mt

from spdoc_mcp.errors import AppError

logger = logging.getLogger(__name__)


class ErrorHandlingMiddleware(Middleware):
    """Reshape a tool's domain :class:`AppError` into a clean MCP tool error."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except ToolError as err:
            cause = err.__cause__
            if isinstance(cause, AppError):
                logger.error("tool_call_failed", extra={"tool": context.message.name}, exc_info=cause)
                raise ToolError(str(cause)) from cause
            raise
