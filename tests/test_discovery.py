from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, BotState, DiscoveryRun, DiscoverySource, Post
from app.services.discovery import DiscoveryOptions, DiscoveryWorkflow, filter_sources, similarity
from app.services.interfaces import GeneratedPost, PublishResult
from app.services.threads import PublicThread


def source(identifier: str, text: str, hours: int = 0, username: str = "author") -> PublicThread:
    return PublicThread(identifier, text, username, datetime.now(timezone.utc) - timedelta(hours=hours))


def options(min_sources: int = 2) -> DiscoveryOptions:
    return DiscoveryOptions(("ии",), 30, 24, min_sources, 1, 20, .82, ())


def test_filtering_freshness_spam_own_seen_and_duplicates() -> None:
    items = [source("1", "Содержательная свежая мысль о развитии технологии сегодня"),
             source("2", "Содержательная свежая мысль о развитии технологии сегодня!"),
             source("3", "Купи курс и получи скидку прямо сейчас"),
             source("4", "Достаточно длинная, но уже старая публикация", 48),
             source("5", "Самостоятельная полезная мысль владельца профиля", username="bot"),
             source("seen", "Ещё одна полезная и достаточно длинная мысль")]
    result = filter_sources(items, cutoff=datetime.now(timezone.utc) - timedelta(hours=24), own_username="bot",
                            seen_ids={"seen"}, seen_hashes=set(), min_length=20, threshold=.82)
    assert len(result) == 1
    assert result[0].id in {"1", "2"}


def test_similarity_detects_near_duplicate() -> None:
    assert similarity("Одна полезная мысль про бизнес", "Одна полезная мысль про бизнес!") > .9


def test_filter_sources_reports_rejection_reasons() -> None:
    now = datetime.now(timezone.utc)
    items = [
        PublicThread("seen", "Достаточно длинный уже использованный текст", "author", now),
        PublicThread("short", "коротко", "author", now),
        PublicThread("own", "Достаточно длинная публикация собственного аккаунта", "owner", now),
    ]
    rejected: dict[str, int] = {}

    result = filter_sources(
        items, cutoff=now - timedelta(hours=1), own_username="owner",
        seen_ids={"seen"}, seen_hashes=set(), min_length=20,
        threshold=.82, rejection_counts=rejected,
    )

    assert result == []
    assert rejected == {"seen_id": 1, "too_short": 1, "own_account": 1}


class FakeGenerator:
    def __init__(self, text: str): self.text = text
    async def generate(self, *_): return GeneratedPost(text=self.text)


class FakeThreads:
    def __init__(self, items): self.items, self.published = items, 0
    async def search_recent(self, *_): return self.items
    async def publish(self, *_): self.published += 1; return PublishResult("external")


class FakeCipher:
    def decrypt(self, value): return "token"


@pytest.mark.asyncio
async def test_skip_and_insufficient_sources_do_not_publish() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from app.services.workflow import PublishingWorkflow
    for text, items, minimum in [("SKIP", [source("1", "Длинная содержательная публикация по теме")], 1),
                                  ("Новый вывод", [source("2", "Длинная содержательная публикация по теме")], 2)]:
        threads = FakeThreads(items)
        generator = FakeGenerator(text)
        publisher = PublishingWorkflow(generator, threads, FakeCipher())
        flow = DiscoveryWorkflow(generator, threads, FakeCipher(), publisher, options(minimum))
        async with factory() as session:
            state = BotState(telegram_id=1, threads_connected=True, threads_access_token="encrypted", threads_user_id="u")
            assert await flow.run(session, state) is None
            assert threads.published == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_success_persists_sources_and_prevents_repeat() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from app.services.workflow import PublishingWorkflow
    items = [source("a", "Развёрнутая мысль о новых рабочих процессах в командах"),
             source("b", "Наблюдение о том как меняется аналитика продуктов")]
    threads, generator = FakeThreads(items), FakeGenerator("Команды меняют подход к работе: инструменты становятся частью процесса, а ценность остаётся в качестве решений.")
    publisher = PublishingWorkflow(generator, threads, FakeCipher())
    flow = DiscoveryWorkflow(generator, threads, FakeCipher(), publisher, options())
    async with factory() as session:
        state = BotState(telegram_id=7, threads_connected=True, threads_access_token="x", threads_user_id="u",
                         threads_token_expires_at=datetime.now(timezone.utc) + timedelta(days=30))
        first = await flow.run(session, state)
        assert first and first.status == "published"
        assert len(list(await session.scalars(__import__('sqlalchemy').select(DiscoverySource)))) == 2
        assert len(list(await session.scalars(__import__('sqlalchemy').select(DiscoveryRun)))) == 1
        assert await flow.run(session, state) is None
        assert threads.published == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_prepare_does_not_publish_until_confirmed() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from app.services.workflow import PublishingWorkflow

    items = [source("manual-a", "Подробная публикация о применении инструментов в работе команд"),
             source("manual-b", "Содержательное наблюдение об изменениях в продуктовой аналитике")]
    threads = FakeThreads(items)
    generator = FakeGenerator("Инструменты меняются быстро, но результат по-прежнему зависит от качества решений команды.")
    publisher = PublishingWorkflow(generator, threads, FakeCipher())
    flow = DiscoveryWorkflow(generator, threads, FakeCipher(), publisher, options())
    async with factory() as session:
        state = BotState(telegram_id=9, threads_connected=True, threads_access_token="x", threads_user_id="u",
                         threads_token_expires_at=datetime.now(timezone.utc) + timedelta(days=30))
        prepared = await flow.prepare(session, state)
        assert prepared is not None
        assert threads.published == 0
        assert not list(await session.scalars(__import__('sqlalchemy').select(DiscoverySource)))

        post = await flow.publish_prepared(session, state, prepared)
        assert post is not None and post.status == "published"
        assert threads.published == 1
        assert len(list(await session.scalars(__import__('sqlalchemy').select(DiscoverySource)))) == 2
    await engine.dispose()
