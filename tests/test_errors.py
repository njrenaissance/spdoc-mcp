"""Unit tests for the domain exception hierarchy."""

from typing import Any

import pytest

from spdoc_mcp.errors import (
    AppError,
    AuthError,
    ConfigError,
    GraphError,
    NotFoundError,
    ToolArgumentError,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc_type",
    [
        pytest.param(ConfigError, id="config"),
        pytest.param(AuthError, id="auth"),
        pytest.param(NotFoundError, id="not_found"),
        pytest.param(ToolArgumentError, id="tool_argument"),
        pytest.param(GraphError, id="graph"),
    ],
)
def test_domain_exceptions_subclass_app_error(exc_type: type[AppError]) -> None:
    assert issubclass(exc_type, AppError)


@pytest.mark.unit
def test_app_error_subclasses_exception() -> None:
    assert issubclass(AppError, Exception)


@pytest.mark.unit
def test_graph_error_stores_context() -> None:
    status = 403
    error = GraphError(
        "boom",
        status_code=status,
        graph_code="accessDenied",
        graph_message="Insufficient privileges",
    )

    assert str(error) == "boom"
    assert error.status_code == status
    assert error.graph_code == "accessDenied"
    assert error.graph_message == "Insufficient privileges"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "expected_code", "expected_message", "expected_str"),
    [
        pytest.param(
            {"error": {"code": "invalidRequest", "message": "bad choice value"}},
            "invalidRequest",
            "bad choice value",
            "Graph request failed with status 400: invalidRequest — bad choice value",
            id="code_and_message",
        ),
        pytest.param(
            {"error": {"code": "invalidRequest"}},
            "invalidRequest",
            None,
            "Graph request failed with status 400: invalidRequest",
            id="code_only",
        ),
        pytest.param(
            {"error": {"message": "bad choice value"}},
            None,
            "bad choice value",
            "Graph request failed with status 400 — bad choice value",
            id="message_only",
        ),
    ],
)
def test_graph_error_from_response_builds_message(
    body: dict[str, Any] | None,
    expected_code: str | None,
    expected_message: str | None,
    expected_str: str,
) -> None:
    status = 400
    error = GraphError.from_response(status, body)

    assert error.status_code == status
    assert error.graph_code == expected_code
    assert error.graph_message == expected_message
    assert str(error) == expected_str


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        pytest.param(None, id="none_body"),
        pytest.param({}, id="empty_body"),
        pytest.param({"error": {}}, id="empty_error"),
        pytest.param({"error": None}, id="null_error"),
        pytest.param({"error": "forbidden"}, id="non_dict_error"),
    ],
)
def test_graph_error_from_response_degrades_gracefully(body: dict[str, Any] | None) -> None:
    status = 500
    error = GraphError.from_response(status, body)

    assert error.status_code == status
    assert error.graph_code is None
    assert error.graph_message is None
    assert str(error) == f"Graph request failed with status {status}"


@pytest.mark.unit
def test_translation_preserves_exception_chain() -> None:
    original = ValueError("root cause")

    with pytest.raises(ConfigError) as excinfo:
        raise ConfigError("wrapped") from original

    assert excinfo.value.__cause__ is original
