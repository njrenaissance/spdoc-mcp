"""Logging configuration for the MCP server.

Structured logging (``structlog``) is disabled for this project (see the profile
in ``CLAUDE.md``), so the standard library ``logging`` module is the sanctioned
choice. Logs are written to **stderr**: under the stdio transport, stdout is the
JSON-RPC channel and any stray write there corrupts the protocol stream.
"""

import logging
import sys

_DEFAULT_LEVEL = logging.INFO


def configure_logging(level: int = _DEFAULT_LEVEL) -> None:
    """Send application logs to stderr at ``level``.

    Idempotent: repeated calls do not stack duplicate handlers on the root
    logger, so it is safe to call once at the process entrypoint without
    guarding the call site.
    """
    root = logging.getLogger()
    root.setLevel(level)
    if any(_is_stderr_handler(handler) for handler in root.handlers):
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


def _is_stderr_handler(handler: logging.Handler) -> bool:
    """Return True if ``handler`` is a stream handler already writing to stderr."""
    return isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr
