"""Application configuration — the single source of truth for all settings.

No os.environ / os.getenv anywhere else in the codebase
(see .claude/standards/configuration.md). Import get_settings() to read config.
"""

import functools
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Every non-secret default in one JSON-serializable dict, keyed by section, so
# tests can override just what they need: Settings(**{**DEFAULTS, "transport": {...}}).
DEFAULTS: dict[str, Any] = {
    "transport": {
        "mode": "stdio",
        "host": "0.0.0.0",  # bind all interfaces (chosen default for remote/HTTP use)
        "port": 8000,
    },
}


class AzureSettings(BaseSettings):
    """Azure app-only client credentials (ADR-0001).

    Env names are fixed by spec/spec.md (Auth > Credentials): AZURE_TENANT_ID,
    AZURE_CLIENT_ID, AZURE_CLIENT_SECRET. This section deliberately uses the
    AZURE_ prefix rather than the app prefix to honor that external contract.
    All three are REQUIRED SecretStr — a missing one crashes at startup.
    """

    model_config = SettingsConfigDict(env_prefix="AZURE_", env_file=".env", extra="ignore")

    tenant_id: SecretStr
    client_id: SecretStr
    client_secret: SecretStr


class TransportSettings(BaseSettings):
    """Non-secret transport/runtime config (ADR-0005: dual stdio/HTTP)."""

    model_config = SettingsConfigDict(env_prefix="SPDOC__TRANSPORT_", env_file=".env", extra="ignore")

    mode: Literal["stdio", "http"] = DEFAULTS["transport"]["mode"]
    host: str = DEFAULTS["transport"]["host"]
    port: int = DEFAULTS["transport"]["port"]


class Settings(BaseSettings):
    """Single source of truth for all configuration."""

    model_config = SettingsConfigDict(env_prefix="SPDOC_", env_file=".env", extra="ignore")

    azure: AzureSettings = Field(default_factory=AzureSettings)
    transport: TransportSettings = Field(default_factory=TransportSettings)


@functools.lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (parsed/validated once)."""
    return Settings()
