"""Unit tests for the FastMCP app factory, registration seam, and error surface."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from spdoc_mcp.errors import NotFoundError
from spdoc_mcp.server import APP_NAME, create_app

SECRET_DETAIL = "secret internal detail"


@pytest.mark.unit
def test_create_app_returns_named_fastmcp() -> None:
    app = create_app()
    assert isinstance(app, FastMCP)
    assert app.name == APP_NAME


@pytest.mark.unit
async def test_scaffold_registers_no_tools() -> None:
    async with Client(create_app()) as client:
        assert await client.list_tools() == []


@pytest.mark.unit
async def test_registration_mechanism_exposes_tool() -> None:
    app = create_app()

    def register(target: FastMCP) -> None:
        @target.tool
        def echo(value: str) -> str:
            return value

    register(app)

    async with Client(app) as client:
        assert [tool.name for tool in await client.list_tools()] == ["echo"]
        result = await client.call_tool("echo", {"value": "hi"})
        assert result.data == "hi"


@pytest.mark.unit
async def test_app_error_surfaces_as_clean_tool_error() -> None:
    app = create_app()

    @app.tool
    def boom() -> str:
        raise NotFoundError("site not found: contoso")

    async with Client(app) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("boom", {})

    message = str(exc_info.value)
    assert message == "site not found: contoso"
    assert "Traceback" not in message
    assert "NotFoundError" not in message


@pytest.mark.unit
async def test_programmer_error_is_masked_not_translated() -> None:
    app = create_app()

    @app.tool
    def prog() -> str:
        raise ValueError(SECRET_DETAIL)

    async with Client(app) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("prog", {})

    assert SECRET_DETAIL not in str(exc_info.value)
