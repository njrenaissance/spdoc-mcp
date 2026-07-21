---
type: Playbook
title: Testing Guide
description: Test structure, patterns, mocking strategies, coverage requirements, and conventions for spdoc-mcp.
---

# Testing Guide

The spdoc-mcp test suite is organized by module and uses pytest with mocking (pytest-mock, respx) to isolate units and mock external dependencies.

## Running Tests

```bash
# Run all tests
uv run pytest

# Run with verbose output and live logs
uv run pytest -v

# Run a specific file
uv run pytest tests/test_auth.py

# Run a specific test
uv run pytest tests/test_auth.py::test_token_caching

# Run with coverage report
uv run pytest --cov

# Run only unit tests (excluding integration tests)
uv run pytest -m unit
```

## Coverage Requirements

- **Minimum threshold:** 70% (enforced in [pyproject.toml](/pyproject.toml)).
- **Report missing lines:** `pytest --cov` shows which lines are not covered.
- **Failure:** if coverage drops below 70%, the test suite fails.

```toml
[tool.coverage.report]
fail_under = 70
show_missing = true
```

## Test Structure

Tests live in `/tests/`:

```
tests/
├── conftest.py                    # pytest configuration, shared fixtures
├── test_auth.py                   # TokenProvider, token lifecycle
├── test_server.py                 # App factory, tool registration
├── test_middleware.py             # Error handling middleware
├── test_settings.py               # Configuration parsing
├── test_errors.py                 # Exception behavior
├── test_logging_config.py         # Logging setup
└── test_main.py                   # Entrypoint (minimal)
```

Each test file mirrors the module it tests. For example, `test_auth.py` tests [auth.py](/src/spdoc_mcp/auth.py).

## Common Patterns

### Fixtures

Shared fixtures live in [conftest.py](/tests/conftest.py). Common patterns:

**Mocked settings:**

```python
@pytest.fixture
def settings_with_creds(monkeypatch):
    """Settings with valid test credentials."""
    return Settings(
        azure=AzureSettings(
            tenant_id=SecretStr("test-tenant"),
            client_id=SecretStr("test-client"),
            client_secret=SecretStr("test-secret"),
        )
    )
```

Use this fixture to override `get_settings()` in tests:

```python
def test_auth_request(settings_with_creds, monkeypatch, respx_mock):
    monkeypatch.setattr("spdoc_mcp.auth.get_settings", lambda: settings_with_creds)
    # Test code
```

**Mocked clock:**

```python
@pytest.fixture
def mock_clock():
    """A controllable mock clock for token expiry testing."""
    clock_time = [0.0]  # mutable container
    def _clock():
        return clock_time[0]
    def _advance(delta):
        clock_time[0] += delta
    _clock.advance = _advance
    return _clock
```

### Async Tests

The test suite uses `pytest-asyncio` with `asyncio_mode = "auto"` (in [pyproject.toml](/pyproject.toml)), so you can mark tests as async without `@pytest.mark.asyncio`:

```python
async def test_token_caching(token_provider, respx_mock):
    """Token is cached and reused on subsequent calls."""
    respx_mock.post("https://login.microsoftonline.com/test-tenant/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token1", "expires_in": 3600})
    )
    
    token1 = await token_provider.get_token()
    token2 = await token_provider.get_token()
    
    assert token1 == token2
    assert respx_mock.calls.count == 1  # Only one network call
```

### Mocking HTTP Requests

Use `respx` to mock httpx requests (installed as a dev dependency):

```python
def test_auth_error_handling(respx_mock, token_provider, settings_with_creds):
    """Azure returns 401 (invalid secret)."""
    respx_mock.post("https://login.microsoftonline.com/test-tenant/oauth2/v2.0/token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )
    
    with pytest.raises(AuthError, match="token acquisition failed"):
        await token_provider.get_token()
```

### Mocking Settings

Use `monkeypatch` to override `get_settings()`:

```python
def test_missing_credential(monkeypatch):
    """ConfigError if AZURE_TENANT_ID is not set."""
    def mock_get_settings():
        raise ConfigError("missing AZURE_TENANT_ID")
    
    monkeypatch.setattr("spdoc_mcp.settings.get_settings", mock_get_settings)
    
    with pytest.raises(ConfigError):
        get_settings()
```

### Testing Exceptions

Test that exceptions carry the right information:

```python
def test_graph_error_from_response():
    """GraphError.from_response parses error body correctly."""
    body = {
        "error": {
            "code": "itemNotFound",
            "message": "The resource could not be found."
        }
    }
    
    err = GraphError.from_response(404, body)
    
    assert err.status_code == 404
    assert err.graph_code == "itemNotFound"
    assert err.graph_message == "The resource could not be found."
    assert "itemNotFound" in str(err)
```

## Test File Examples

### test_auth.py

Tests the TokenProvider and auth lifecycle:

- Token acquisition and caching
- Refresh on expiry
- Refresh margin (300 seconds early)
- Double-checked locking (concurrent calls)
- Monotonic clock
- Event loop binding
- Error handling (network errors, Azure 401, etc.)

Example test:

```python
async def test_refresh_before_expiry(token_provider, respx_mock, mock_clock):
    """Token is refreshed 300 seconds before stated expiry."""
    token_provider._clock = mock_clock
    
    # Acquire token with 600-second lifetime
    respx_mock.post(...).mock(
        return_value=httpx.Response(200, json={
            "access_token": "token1",
            "expires_in": 600
        })
    )
    token1 = await token_provider.get_token()
    
    # Advance 310 seconds (within 300-second margin)
    mock_clock.advance(310)
    
    # Next call refreshes the token
    respx_mock.post(...).mock(
        return_value=httpx.Response(200, json={
            "access_token": "token2",
            "expires_in": 600
        })
    )
    token2 = await token_provider.get_token()
    
    assert token1 != token2
```

### test_settings.py

Tests configuration parsing and validation:

- Required credentials present
- Missing credentials raise `ConfigError`
- Transport config defaults
- Environment variable override

Example test:

```python
def test_missing_tenant_id(monkeypatch):
    """ConfigError if AZURE_TENANT_ID is missing."""
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    
    with pytest.raises(ConfigError, match="AZURE_TENANT_ID"):
        get_settings()
```

### test_middleware.py

Tests error handling at the tool boundary:

- Domain errors are logged and converted to clean MCP errors
- Programmer errors are masked

Example test:

```python
def test_domain_error_handling(middleware):
    """AppError is logged and converted to ToolError."""
    tool_error = ToolError("FastMCP error")
    tool_error.__cause__ = NotFoundError("document not found")
    
    with pytest.raises(ToolError, match="document not found"):
        middleware.on_call_tool(context, lambda: tool_error)
```

### test_server.py

Tests the app factory and tool registration:

- App is created with error handling middleware
- Tools can be registered
- Logging is configured

Example test:

```python
def test_app_factory():
    """create_app builds the FastMCP instance with middleware."""
    app = create_app()
    assert isinstance(app, FastMCP)
    assert any(isinstance(m, ErrorHandlingMiddleware) for m in app._middleware)
```

## Best Practices

### Isolation

- Mock external dependencies (Azure, Graph API).
- Use fixtures to avoid duplication.
- Test one behavior per test function.

### Clarity

- Use descriptive test names: `test_<behavior>_<given_condition>`.
- Include a docstring explaining what is being tested.
- Use `pytest.raises` for expected exceptions.

### Concurrency

- Async tests should not depend on order or timing.
- Use `mock_clock` to control time-based logic.
- Test double-checked locking by awaiting multiple concurrent calls.

### Coverage

- Aim for 100% line coverage for core logic (auth, error handling, config).
- Aim for ≥70% overall (minimum requirement).
- Use `uv run pytest --cov` to identify gaps.

## Adding New Tests

When adding a new module (e.g., a tool):

1. Create a `test_<module>.py` file in `/tests/`.
2. Import the module under test.
3. Write fixtures for any setup needed (mocked settings, mocked Graph, etc.).
4. Write tests for each public function/class.
5. Run `uv run pytest` to verify coverage.

Example:

```python
# tests/test_list_documents.py
import pytest
from spdoc_mcp.tools.list_documents import register, list_documents

@pytest.fixture
def app():
    """Create a FastMCP app with the list_documents tool registered."""
    from spdoc_mcp.server import create_app
    app = create_app()
    register(app)
    return app

async def test_list_documents(app, respx_mock, settings_with_creds, monkeypatch):
    """list_documents returns document list from Graph."""
    monkeypatch.setattr("spdoc_mcp.settings.get_settings", lambda: settings_with_creds)
    respx_mock.get("https://graph.microsoft.com/v1.0/sites/test-site-id/lists/test-list-id/items").mock(
        return_value=httpx.Response(200, json={
            "value": [
                {"id": "1", "fields": {"Title": "Doc1"}},
                {"id": "2", "fields": {"Title": "Doc2"}},
            ]
        })
    )
    
    result = await list_documents(site_id="test-site-id", list_id="test-list-id")
    
    assert len(result) == 2
    assert result[0]["Title"] == "Doc1"
```

## Debugging Tests

### Verbose Output

```bash
uv run pytest -v -s
```

- `-v` — verbose test names and results
- `-s` — show print statements and logs

### Breakpoint

Add `breakpoint()` in a test to drop into the Python debugger:

```python
async def test_something():
    result = await something()
    breakpoint()  # Debugger stops here
    assert result == expected
```

### Keep Logs

By default, pytest captures and hides logs. Show them with:

```bash
uv run pytest --log-cli-level=INFO
```

## Continuous Integration

CI runs tests on every push via `.github/workflows/ci.yml`:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

If any step fails, the PR build fails. Ensure tests pass locally before pushing:

```bash
uv run pytest && uv run ruff check . && uv run mypy src
```

---

**Generated by OpenWiki.** See `.claude/standards/testing.md` for project testing standards.
