from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    telegram_bot_token: str = Field(min_length=1)
    admin_telegram_ids: Annotated[frozenset[int], NoDecode]
    database_url: str = Field(min_length=1)
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    openrouter_timeout_seconds: float = Field(default=30, gt=0, le=120)
    default_system_prompt: str = "Создай полезный, естественный пост для Threads на русском языке. Без выдуманных фактов."
    threads_client_id: str = ""
    threads_client_secret: str = ""
    threads_redirect_uri: str = ""
    token_encryption_key: str = ""
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    threads_api_version: str = "v1.0"
    log_level: str = "INFO"
    default_post_interval_minutes: int = Field(default=60, ge=5, le=10080)
    threads_discovery_enabled: bool = False
    threads_discovery_keywords: Annotated[tuple[str, ...], NoDecode] = (
        "искусственный интеллект", "бизнес", "спорт", "аналитика"
    )
    threads_discovery_search_limit: int = Field(default=30, ge=1, le=100)
    threads_discovery_lookback_hours: int = Field(default=24, ge=1, le=168)
    threads_discovery_min_sources: int = Field(default=5, ge=1, le=100)
    threads_discovery_max_posts_per_run: int = Field(default=1, ge=0, le=5)
    threads_discovery_min_text_length: int = Field(default=40, ge=1, le=500)
    threads_discovery_similarity_threshold: float = Field(default=0.82, ge=0.5, le=1)
    threads_discovery_blocked_phrases: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("threads_discovery_keywords", "threads_discovery_blocked_phrases", mode="before")
    @classmethod
    def parse_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
