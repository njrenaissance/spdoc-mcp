"""MCP tool modules.

Each module here exposes ``def register(app: FastMCP) -> None`` that declares its
tool(s) with ``@app.tool``; :func:`spdoc_mcp.server._register_tools` calls each one.
Keeping declaration out of import time (no side effects) gives the four tool issues
(#10-#13) a single, explicit seam to plug into.
"""
