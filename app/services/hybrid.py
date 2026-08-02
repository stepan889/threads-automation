import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotState, NewsItem
from app.repository import available_news, last_published_post, recent_posts
from app.services.interfaces import GeneratedPost
from app.services.errors import EmptyGenerationError
from app.services.news import NewsService
from app.services.agentapi import AgentApiPostGenerator

logger = logging.getLogger(__name__)

EMERGENCY_POSTS = (
    "Иногда лучший способ разобраться в новой теме — попробовать объяснить её простыми словами. А какой навык вы сейчас осваиваете?",
    "Полезные идеи часто появляются не во время работы, а в короткой паузе. Что помогает вам перезагрузиться и снова собраться?",
    "Можно долго ждать идеального момента, но небольшой шаг сегодня обычно полезнее большого плана на потом. Что вы давно откладываете?",
    "Любопытство — хороший повод попробовать что-то новое без ожидания мгновенного результата. Что вам хотелось бы изучить просто ради интереса?",
    "Иногда один честный вопрос помогает больше, чем десять готовых советов. Какой вопрос вы бы сегодня задали человеку с большим опытом?",
)


@dataclass(frozen=True)
class GeneratedDraft:
    text: str
    post_type: str = "regular"
    news_item_id: int | None = None
    news_source: str | None = None
    news_url: str | None = None


class HybridPostGenerator:
    def __init__(self, generator: AgentApiPostGenerator, news: NewsService, news_percent: int, lookback_hours: int, rng=None) -> None:
        self.generator, self.news = generator, news
        self.news_percent, self.lookback_hours = news_percent, lookback_hours
        self.rng = rng or random.SystemRandom()

    async def generate(self, session: AsyncSession, state: BotState) -> GeneratedDraft:
        history = [post.content for post in await recent_posts(session, state.telegram_id, 5) if post.content]
        last = await last_published_post(session, state.telegram_id)
        wants_news = self.news_percent > 0 and (not last or last.post_type != "news") and self.rng.randrange(100) < self.news_percent
        if wants_news:
            await self.news.refresh(session)
            candidates = await available_news(session, datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours))
            if candidates:
                item = self.rng.choice(candidates)
                prompt = self._news_prompt(item, history)
                try:
                    generated = await self.generator.generate(prompt, state.system_prompt)
                    return GeneratedDraft(generated.text, "news", item.id, item.source, item.url)
                except EmptyGenerationError:
                    logger.warning(
                        "Empty news generation; falling back to regular post source=%s news_item_id=%s",
                        item.source, item.id,
                    )
        return await self._generate_regular(state, history)

    async def _generate_regular(self, state: BotState, history: list[str]) -> GeneratedDraft:
        prompt = state.topic or "Создай новый пост по актуальной теме профиля."
        if history:
            prompt += "\n\nНе повторяй идеи и формулировки недавних публикаций:\n" + "\n".join(f"- {x[:300]}" for x in history)
        try:
            generated: GeneratedPost = await self.generator.generate(prompt, state.system_prompt)
            return GeneratedDraft(generated.text)
        except EmptyGenerationError:
            logger.warning("Empty regular generation; retrying with compact prompt and low reasoning effort")
        try:
            generated = await self.generator.generate(
                "Напиши один короткий живой пост-вопрос для аудитории на русском языке. Без новостей, фактов, ссылок и вступлений.",
                "Создай естественный самостоятельный текст для Threads. Ответь только готовым постом до 500 символов.",
                reasoning_effort="low",
            )
            return GeneratedDraft(generated.text)
        except EmptyGenerationError:
            logger.warning("AgentAPI retry was empty; using local emergency post")
            recent = {text.strip() for text in history}
            choices = [text for text in EMERGENCY_POSTS if text not in recent] or list(EMERGENCY_POSTS)
            return GeneratedDraft(self.rng.choice(choices))

    @staticmethod
    def _news_prompt(item: NewsItem, history: list[str]) -> str:
        prompt = f"""Создай короткий самостоятельный пост для Threads на естественном разговорном русском языке.
Новость — только повод для обсуждения, а не пересказ, сводка или аналитический вывод.
Возьми ровно один главный факт из данных ниже, затем добавь живую реакцию, наблюдение или вопрос аудитории.
Не называй автора статьи, не добавляй ссылку, не копируй формулировки, не выдумывай факты, числа, свойства или цитаты. Избегай канцелярита и шаблонного вступления.

Заголовок: {item.title}
Краткое описание: {item.description}"""
        if history:
            prompt += "\nНе повторяй недавние публикации:\n" + "\n".join(f"- {x[:300]}" for x in history)
        return prompt
