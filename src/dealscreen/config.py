"""Runtime configuration, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix=""
    )

    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-5-5", alias="DEALSCREEN_MODEL")
    anthropic_web_search: bool = Field(default=False, alias="DEALSCREEN_ANTHROPIC_WEB_SEARCH")
    companies_house_api_key: SecretStr | None = Field(default=None, alias="COMPANIES_HOUSE_API_KEY")

    data_dir: Path = Field(default=Path(".dealscreen"), alias="DEALSCREEN_DATA_DIR")
    cache_ttl_seconds: int = Field(default=24 * 3600, alias="DEALSCREEN_CACHE_TTL")
    http_timeout_seconds: float = Field(default=15.0, alias="DEALSCREEN_HTTP_TIMEOUT")
    max_agent_turns: int = Field(default=14, alias="DEALSCREEN_MAX_TURNS")
    max_output_tokens: int = Field(default=4096, alias="DEALSCREEN_MAX_OUTPUT_TOKENS")
    batch_concurrency: int = Field(default=2, alias="DEALSCREEN_BATCH_CONCURRENCY")
    trusted_egress_cidrs: str = Field(default="", alias="DEALSCREEN_TRUSTED_EGRESS_CIDRS")
    user_agent: str = Field(
        default="dealscreen/0.1 (+https://github.com/ahyanadatia/dealscreen)",
        alias="DEALSCREEN_USER_AGENT",
    )

    @property
    def llm_enabled(self) -> bool:
        key = self.anthropic_api_key.get_secret_value() if self.anthropic_api_key else ""
        return bool(key) and not key.startswith("sk-ant-placeholder")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dealscreen.sqlite3"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
