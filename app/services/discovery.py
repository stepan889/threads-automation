from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotState, DiscoveryRun, DiscoverySource, Post
from app.services.crypto import TokenCipher
from app.services.errors import ServiceError
from app.services.openrouter import THREADS_TEXT_LIMIT, OpenRouterPostGenerator
from app.services.threads import PublicThread, ThreadsClient
from app.services.workflow import PublishingWorkflow

logger = logging.getLogger(__name__)
URL_RE = re.compile(r"https?://\S+", re.I)
NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?!\w)")
QUOTE_RE = re.compile(r"[«»\"]")
SPAM = ("купи", "промокод", "скидк", "заработок без", "подпишись", "розыгрыш", "реклама")


def normalize(text: str) -> str:
    return " ".join(URL_RE.sub("", text).lower().split())


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


@dataclass(frozen=True)
class DiscoveryOptions:
    keywords: tuple[str, ...]
    search_limit: int
    lookback_hours: int
    min_sources: int
    max_posts_per_run: int
    min_text_length: int
    similarity_threshold: float
    blocked_phrases: tuple[str, ...]


@dataclass(frozen=True)
class PreparedDiscovery:
    text: str
    sources: tuple[PublicThread, ...]


def filter_sources(items: list[PublicThread], *, cutoff: datetime, own_username: str | None,
                   seen_ids: set[str], seen_hashes: set[str], min_length: int,
                   threshold: float, rejection_counts: dict[str, int] | None = None) -> list[PublicThread]:
    selected: list[PublicThread] = []
    for item in sorted(items, key=lambda value: value.timestamp, reverse=True):
        text = normalize(item.text)
        stamp = item.timestamp if item.timestamp.tzinfo else item.timestamp.replace(tzinfo=timezone.utc)
        reason = None
        if item.id in seen_ids:
            reason = "seen_id"
        elif text_hash(text) in seen_hashes:
            reason = "seen_text"
        elif stamp < cutoff:
            reason = "too_old"
        elif len(text) < min_length:
            reason = "too_short"
        elif own_username and (item.username or "").casefold() == own_username.casefold():
            reason = "own_account"
        elif any(marker in text for marker in SPAM):
            reason = "spam"
        elif text.startswith(("repost ", "ответ @", "цитата @")):
            reason = "reply_or_repost"
        elif any(similarity(text, existing.text) >= threshold for existing in selected):
            reason = "duplicate"
        if reason:
            if rejection_counts is not None:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            continue
        selected.append(item)
    return selected


class DiscoveryWorkflow:
    def __init__(self, generator: OpenRouterPostGenerator, threads: ThreadsClient, cipher: TokenCipher,
                 publisher: PublishingWorkflow, options: DiscoveryOptions) -> None:
        self.generator, self.threads, self.cipher, self.publisher, self.options = generator, threads, cipher, publisher, options

    async def run(self, session: AsyncSession, state: BotState) -> Post | None:
        logger.info("discovery_cycle_start telegram_id=%s topics=%s", state.telegram_id, list(self.options.keywords))
        if self.options.max_posts_per_run < 1:
            return None
        prepared = await self.prepare(session, state)
        if prepared is None:
            return None
        return await self.publish_prepared(session, state, prepared)

    async def prepare(self, session: AsyncSession, state: BotState) -> PreparedDiscovery | None:
        """Collect, validate and generate a discovery post without publishing it."""
        if not state.threads_access_token or not state.threads_connected:
            raise ServiceError("Аккаунт Threads не подключён.")
        token = self.cipher.decrypt(state.threads_access_token)
        expires = state.threads_token_expires_at
        if expires is not None:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc):
                raise ServiceError("Токен Threads истёк. Подключите аккаунт заново.")
            if expires <= datetime.now(timezone.utc) + timedelta(days=7):
                token, expires = await self.threads.refresh(token)
                state.threads_access_token = self.cipher.encrypt(token)
                state.threads_token_expires_at = expires
                await session.commit()
        raw: list[PublicThread] = []
        for keyword in self.options.keywords:
            raw.extend(await self.threads.search_recent(keyword, token, self.options.search_limit))
        existing = list(await session.scalars(select(DiscoverySource).where(DiscoverySource.telegram_id == state.telegram_id)))
        rejection_counts: dict[str, int] = {}
        filtered = filter_sources(raw, cutoff=datetime.now(timezone.utc) - timedelta(hours=self.options.lookback_hours),
                                  own_username=state.threads_username, seen_ids={x.source_id for x in existing},
                                  seen_hashes={x.text_hash for x in existing}, min_length=self.options.min_text_length,
                                  threshold=self.options.similarity_threshold, rejection_counts=rejection_counts)
        logger.info("discovery_sources telegram_id=%s found=%s filtered=%s rejected=%s",
                    state.telegram_id, len(raw), len(filtered), rejection_counts)
        if len(filtered) < self.options.min_sources:
            logger.info("discovery_skip telegram_id=%s reason=insufficient_sources", state.telegram_id)
            return None
        prompt = ("Проанализируй свежие публикации Threads ниже. Выдели общую содержательную тему и создай самостоятельный "
                  "русскоязычный пост. Не копируй формулировки, не называй авторов, не выдумывай факты, числа, имена или цитаты. "
                  "Если данных недостаточно или они противоречат друг другу, ответь только SKIP.\n\n" +
                  "\n---\n".join(item.text for item in filtered))
        generated = await self.generator.generate(prompt, state.system_prompt)
        candidate = generated.text.strip()
        recent = list(await session.scalars(select(Post).where(Post.telegram_id == state.telegram_id, Post.status == "published").order_by(Post.created_at.desc()).limit(20)))
        reason = self._invalid_reason(candidate, filtered, recent)
        if reason:
            logger.info("discovery_skip telegram_id=%s reason=%s", state.telegram_id, reason)
            return None
        return PreparedDiscovery(candidate, tuple(filtered))

    async def publish_prepared(self, session: AsyncSession, state: BotState,
                               prepared: PreparedDiscovery) -> Post | None:
        """Idempotently publish a previously reviewed discovery draft."""
        candidate, filtered = prepared.text, list(prepared.sources)
        digest = text_hash(candidate)
        run = DiscoveryRun(telegram_id=state.telegram_id, content_hash=digest, status="reserved")
        session.add(run)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.info("discovery_skip telegram_id=%s reason=duplicate_content", state.telegram_id)
            return None
        post = await self.publisher.publish_text(session, state, candidate)
        run.status, run.post_id = post.status, post.id
        if post.status == "published":
            for item in filtered:
                session.add(DiscoverySource(source_id=item.id, telegram_id=state.telegram_id,
                            text_hash=text_hash(item.text), source_timestamp=item.timestamp))
        else:
            run.diagnostic = post.diagnostic
        await session.commit()
        logger.info("discovery_cycle_end telegram_id=%s status=%s post_id=%s external_id=%s", state.telegram_id, post.status, post.id, post.external_id)
        return post

    def _invalid_reason(self, text: str, sources: list[PublicThread], recent: list[Post]) -> str | None:
        lowered = text.casefold()
        if lowered == "skip" or lowered.startswith("skip\n"): return "model_skip"
        if not text: return "empty"
        if len(text) > THREADS_TEXT_LIMIT: return "too_long"
        if any(phrase.casefold() in lowered for phrase in self.options.blocked_phrases): return "blocked_phrase"
        if NUMBER_RE.search(text) or QUOTE_RE.search(text): return "unverified_claim_marker"
        if any(similarity(text, item.text) >= self.options.similarity_threshold for item in sources): return "source_similarity"
        if any(similarity(text, item.content) >= self.options.similarity_threshold for item in recent): return "recent_similarity"
        return None
