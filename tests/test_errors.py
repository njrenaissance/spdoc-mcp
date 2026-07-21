"""Unit tests for the domain exception hierarchy."""

import pytest

from spdoc_mcp.errors import (
    AppError,
    AuthError,
    ConfigError,
    GraphError,
    NotFoundError,
    ValidationError,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc_type",
    [
        pytest.param(ConfigError, id="config"),
        pytest.param(AuthError, id="auth"),
        pytest.param(NotFoundError, id="not_found"),
        pytest.param(ValidationError, id="validation"),
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
def test_graph_error_from_response_parses_body() -> None:
    status = 400
    body = {"error": {"code": "invalidRequest", "message": "bad choice value"}}

    error = GraphError.from_response(status, body)

    assert error.status_code == status
    assert error.graph_code == "invalidRequest"
    assert error.graph_message == "bad choice value"
    assert str(error) == "Graph request failed with status 400: invalidRequest — bad choice value"


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        pytest.param(None, id="none_body"),
        pytest.param({}, id="empty_body"),
        pytest.param({"error": {}}, id="empty_error"),
    ],
)
def test_graph_error_from_response_degrades_gracefully(body: dict | None) -> None:
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
