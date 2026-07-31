from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GeneratedPost:
    text: str


@dataclass(frozen=True)
class PublishResult:
    external_id: str


class PostGenerator(Protocol):
    async def generate(self, prompt: str | None = None, system_prompt: str | None = None) -> GeneratedPost: ...


class ThreadsPublisher(Protocol):
    async def publish(self, user_id: str, access_token: str, text: str) -> PublishResult: ...


class ThreadsConnector(Protocol):
    async def get_authorization_url(self, telegram_id: int) -> str: ...
