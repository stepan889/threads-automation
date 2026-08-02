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
    agentapi_key: str = ""
    agentapi_model: str = ""
    agentapi_timeout_seconds: float = Field(default=30, gt=0, le=120)
    default_system_prompt: str = "Создай полезный, естественный пост для Threads на русском языке. Без выдуманных фактов."
    threads_client_id: str = ""
    threads_client_secret: str = ""
    threads_redirect_uri: str = ""
    token_encryption_key: str = ""
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    threads_api_version: str = "v1.0"
    log_level: str = "INFO"
    default_post_interval_minutes: int = Field(default=60, ge=5, le=10080)
    guardian_api_key: str = ""
    news_post_percent: int = Field(default=30, ge=0, le=100)
    news_lookback_hours: int = Field(default=48, ge=1, le=720)
    news_topics: Annotated[tuple[str, ...], NoDecode] = (
        "бизнес", "искусственный интеллект", "нейросети", "аналитика", "спорт",
        "технологии", "интернет-продукты", "стартапы", "наука", "инновации",
        "финансы", "маркетинг", "образование", "продуктивность", "культура"
    )
    news_blocked_phrases: Annotated[tuple[str, ...], NoDecode] = (
        "погиб", "гибель", "убит", "убийство", "смерть", "скончался", "война",
        "военный конфликт", "теракт", "терроризм", "катастрофа", "авария", "крушение",
        "взрыв", "стрельба", "нападение", "жертвы", "ранен", "насилие", "трагедия",
        "неподтвержденный слух", "неподтверждённый слух", "шок", "сенсация"
    )

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("news_topics", "news_blocked_phrases", mode="before")
    @classmethod
    def parse_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip().lower() for item in value.split(",") if item.strip())
        return value

@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
