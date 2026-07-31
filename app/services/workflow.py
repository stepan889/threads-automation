from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotState, Post
from app.services.crypto import TokenCipher
from app.services.errors import ServiceError
from app.services.openrouter import OpenRouterPostGenerator
from app.services.threads import ThreadsClient


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class PublishingWorkflow:
    def __init__(self, generator: OpenRouterPostGenerator, threads: ThreadsClient, cipher: TokenCipher) -> None:
        self.generator, self.threads, self.cipher = generator, threads, cipher

    async def generate(self, state: BotState):
        prompt = state.topic or "Создай новый пост по актуальной теме профиля."
        return await self.generator.generate(prompt, state.system_prompt)

    async def publish_text(self, session: AsyncSession, state: BotState, text: str) -> Post:
        post = Post(telegram_id=state.telegram_id, content=text, status="publishing")
        session.add(post)
        await session.commit()
        try:
            if not state.threads_connected or not state.threads_access_token or not state.threads_user_id:
                raise ServiceError("Аккаунт Threads не подключён.")
            token = self.cipher.decrypt(state.threads_access_token)
            expires = _aware(state.threads_token_expires_at)
            if expires is None or expires <= datetime.now(timezone.utc):
                raise ServiceError("Токен Threads истёк. Подключите аккаунт заново.")
            if expires <= datetime.now(timezone.utc) + timedelta(days=7):
                token, expires = await self.threads.refresh(token)
                state.threads_access_token = self.cipher.encrypt(token)
                state.threads_token_expires_at = expires
            result = await self.threads.publish(state.threads_user_id, token, text)
            post.status, post.external_id, post.published_at = "published", result.external_id, datetime.now(timezone.utc)
        except ServiceError as exc:
            post.status, post.diagnostic = "failed", str(exc)[:1000]
        await session.commit()
        return post

    async def run(self, session: AsyncSession, state: BotState) -> Post:
        try:
            generated = await self.generate(state)
        except ServiceError as exc:
            post = Post(telegram_id=state.telegram_id, content="", status="failed", diagnostic=str(exc)[:1000])
            session.add(post)
            await session.commit()
            return post
        return await self.publish_text(session, state, generated.text)
