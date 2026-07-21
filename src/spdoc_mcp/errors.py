"""Project exception hierarchy.

All spdoc-mcp exceptions subclass :class:`AppError`, so a caller can catch one
failure mode without swallowing the rest — never raise or catch bare
``Exception``. When translating a lower-level error into a domain one, always
chain it: ``raise ConfigError(...) from original_err``. Dropping the ``from``
clause breaks the exception chain and hides the real root cause from whoever
reads the traceback.
"""

from typing import Any


class AppError(Exception):
    """Base class for every spdoc-mcp domain exception."""


class ConfigError(AppError):
    """Missing or invalid configuration (e.g. an unset credential env var)."""


class AuthError(AppError):
    """OAuth token acquisition or refresh failed."""


class NotFoundError(AppError):
    """A site, library, document, or column could not be resolved."""


class ValidationError(AppError):
    """Bad tool arguments — e.g. a choice value not in the column's allowed set."""


class GraphError(AppError):
    """A non-2xx response from Microsoft Graph.

    Carries the HTTP status plus the Graph error ``code``/``message`` from the
    response body so a caller can surface a clean message instead of a raw
    stack trace.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        graph_code: str | None = None,
        graph_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.graph_code = graph_code
        self.graph_message = graph_message

    @classmethod
    def from_response(cls, status_code: int, body: dict[str, Any] | None) -> "GraphError":
        """Build a :class:`GraphError` from a status code and a parsed error body.

        Graph error bodies have the shape ``{"error": {"code", "message", ...}}``.
        A missing or empty body degrades gracefully to a status-only message.
        """
        error = (body or {}).get("error", {})
        graph_code = error.get("code")
        graph_message = error.get("message")
        message = f"Graph request failed with status {status_code}"
        if graph_code:
            message += f": {graph_code}"
        if graph_message:
            message += f" — {graph_message}"
        return cls(
            message,
            status_code=status_code,
            graph_code=graph_code,
            graph_message=graph_message,
        )
