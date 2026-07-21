"""Unit tests for the system-boundary error-handling middleware."""

import logging

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from spdoc_mcp.errors import ToolArgumentError
from spdoc_mcp.server import create_app


@pytest.mark.unit
async def test_boundary_logs_full_exception(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app()

    @app.tool
    def boom() -> str:
        raise ToolArgumentError("bad choice value")

    with caplog.at_level(logging.ERROR, logger="spdoc_mcp.middleware"):
        async with Client(app) as client:
            with pytest.raises(ToolError):
                await client.call_tool("boom", {})

    records = [record for record in caplog.records if record.name == "spdoc_mcp.middleware"]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert record.exc_info[0] is ToolArgumentError
