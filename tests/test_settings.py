"""Unit tests for the configuration settings module."""

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from spdoc_mcp.settings import (
    DEFAULTS,
    AzureSettings,
    Settings,
    TransportSettings,
    get_settings,
)

AZURE_ENV_VARS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
TRANSPORT_ENV_VARS = ("SPDOC__TRANSPORT_MODE", "SPDOC__TRANSPORT_HOST", "SPDOC__TRANSPORT_PORT")
OVERRIDE_PORT = 9000


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate each test from the ambient environment and the cached singleton."""
    for name in (*AZURE_ENV_VARS, *TRANSPORT_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_azure_env(
    monkeypatch: pytest.MonkeyPatch,
    tenant: str = "tenant",
    client: str = "client",
    secret: str = "secret",
) -> None:
    """Populate all three required Azure credential env vars."""
    monkeypatch.setenv("AZURE_TENANT_ID", tenant)
    monkeypatch.setenv("AZURE_CLIENT_ID", client)
    monkeypatch.setenv("AZURE_CLIENT_SECRET", secret)


@pytest.mark.unit
@pytest.mark.parametrize("missing", [pytest.param(name, id=name.lower()) for name in AZURE_ENV_VARS])
def test_missing_azure_credential_raises(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    for name in AZURE_ENV_VARS:
        if name != missing:
            monkeypatch.setenv(name, "value")
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ValidationError):
        AzureSettings(_env_file=None)


@pytest.mark.unit
def test_all_azure_credentials_missing_raises() -> None:
    with pytest.raises(ValidationError):
        AzureSettings(_env_file=None)


@pytest.mark.unit
def test_azure_credentials_load_and_redact(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_azure_env(monkeypatch, secret="super-secret-value")
    azure = AzureSettings(_env_file=None)
    assert azure.tenant_id.get_secret_value() == "tenant"
    assert azure.client_secret.get_secret_value() == "super-secret-value"
    assert "super-secret-value" not in repr(azure)
    assert str(azure.client_secret) == "**********"


@pytest.mark.unit
def test_transport_defaults_match_defaults_dict() -> None:
    transport = TransportSettings(_env_file=None)
    assert transport.mode == DEFAULTS["transport"]["mode"]
    assert transport.host == DEFAULTS["transport"]["host"]
    assert transport.port == DEFAULTS["transport"]["port"]


@pytest.mark.unit
def test_transport_env_override_and_coercion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPDOC__TRANSPORT_MODE", "http")
    monkeypatch.setenv("SPDOC__TRANSPORT_PORT", str(OVERRIDE_PORT))
    transport = TransportSettings(_env_file=None)
    assert transport.mode == "http"
    assert transport.port == OVERRIDE_PORT


@pytest.mark.unit
@pytest.mark.parametrize(
    ("var", "value"),
    [
        pytest.param("SPDOC__TRANSPORT_MODE", "grpc", id="invalid_mode"),
        pytest.param("SPDOC__TRANSPORT_PORT", "not-a-number", id="invalid_port"),
    ],
)
def test_invalid_transport_value_raises(monkeypatch: pytest.MonkeyPatch, var: str, value: str) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        TransportSettings(_env_file=None)


@pytest.mark.unit
def test_settings_constructed_from_defaults_and_explicit_azure() -> None:
    settings = Settings(
        azure={"tenant_id": "t", "client_id": "c", "client_secret": "s"},
        **DEFAULTS,
    )
    assert settings.transport.mode == DEFAULTS["transport"]["mode"]
    assert settings.transport.port == DEFAULTS["transport"]["port"]
    assert settings.azure.tenant_id.get_secret_value() == "t"


@pytest.mark.unit
def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_azure_env(monkeypatch)
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
    assert get_settings() is not first
