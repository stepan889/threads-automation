from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, BotState, NewsItem
from app.config import Settings
from app.bot.handlers import _generate_preview
from app.services.errors import EmptyGenerationError, ServiceError
from app.services.hybrid import EMERGENCY_POSTS, HybridPostGenerator
from app.services.interfaces import GeneratedPost
from app.services.news import (BBCNewsClient, GuardianNewsClient, NewsFilter, NewsRecord,
                               NewsService, parse_bbc_rss, parse_guardian_response, stable_hash)


NOW = datetime.now(timezone.utc)


def record(title="AI startup launches new technology", url="https://example.test/a", hours=1):
    return NewsRecord(stable_hash(title), title, "Science and business research", url, "BBC News", NOW - timedelta(hours=hours))


def test_parse_bbc_rss_removes_html():
    xml = f"""<rss><channel><item><title>New AI tool</title><description>&lt;b&gt;Useful&lt;/b&gt; tech</description>
    <link>https://bbc.test/a</link><pubDate>{NOW.strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate></item></channel></rss>"""
    result = parse_bbc_rss(xml)
    assert result[0].description == "Useful tech"
    assert result[0].source == "BBC News"


def test_parse_guardian_response():
    data = {"response": {"results": [{"webTitle": "Science update", "webUrl": "https://g.test/a",
        "webPublicationDate": NOW.isoformat(), "fields": {"trailText": "<p>New research</p>"}}]}}
    result = parse_guardian_response(data)
    assert result[0].description == "New research"
    assert result[0].source == "The Guardian"


@pytest.mark.asyncio
async def test_guardian_without_key_makes_no_request():
    transport = httpx.MockTransport(lambda request: pytest.fail("request must not be made"))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await GuardianNewsClient("", client=client).fetch(48) == []


def test_news_filter_age_and_blocked_phrases():
    news_filter = NewsFilter(("технологии",), ("война",), 48)
    assert news_filter.accepts(record())
    assert news_filter.accepts(record(title="New technology software for business"))
    assert not news_filter.accepts(record(hours=49))
    assert not news_filter.accepts(record(title="Technology war update"))


@pytest.mark.asyncio
async def test_source_failure_does_not_hide_other_source():
    def handler(request):
        if "bbc" in request.url.host:
            return httpx.Response(503)
        return httpx.Response(200, json={"response": {"results": []}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await BBCNewsClient(client=client).fetch() == []
        assert await GuardianNewsClient("key", client=client).fetch(48) == []


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_news_service_deduplicates_url_hash_and_similar_titles(session_factory):
    bbc = SimpleNamespace(fetch=AsyncMock(return_value=[record(), record(url="https://example.test/b")]), close=AsyncMock())
    guardian = SimpleNamespace(fetch=AsyncMock(return_value=[record("AI startup launches new technologies", "https://g.test/a")]), close=AsyncMock())
    service = NewsService(bbc, guardian, NewsFilter(("technology",), (), 48))
    async with session_factory() as session:
        assert await service.refresh(session) == 1
        assert await service.refresh(session) == 0


class FixedRng:
    def __init__(self, roll=0): self.roll = roll
    def randrange(self, value): return self.roll
    def choice(self, values): return values[0]


@pytest.mark.asyncio
async def test_hybrid_percentage_and_fallback(session_factory):
    generator = SimpleNamespace(generate=AsyncMock(return_value=GeneratedPost(text="обычный")))
    news = SimpleNamespace(refresh=AsyncMock(return_value=0))
    state = BotState(telegram_id=1, interval_minutes=60)
    async with session_factory() as session:
        session.add(state); await session.commit()
        zero = HybridPostGenerator(generator, news, 0, 48, FixedRng(0))
        assert (await zero.generate(session, state)).post_type == "regular"
        always = HybridPostGenerator(generator, news, 100, 48, FixedRng(0))
        draft = await always.generate(session, state)
        assert draft.post_type == "regular"
        news.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_two_news_posts_in_a_row(session_factory):
    from app.models import Post
    generator = SimpleNamespace(generate=AsyncMock(return_value=GeneratedPost(text="text")))
    news = SimpleNamespace(refresh=AsyncMock(return_value=0))
    state = BotState(telegram_id=1, interval_minutes=60)
    async with session_factory() as session:
        session.add_all([state, Post(telegram_id=1, content="old", status="published", post_type="news", published_at=NOW)])
        await session.commit()
        draft = await HybridPostGenerator(generator, news, 100, 48, FixedRng()).generate(session, state)
        assert draft.post_type == "regular"
        news.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_news_candidate_creates_auditable_preview_without_using_it(session_factory):
    generator = SimpleNamespace(generate=AsyncMock(return_value=GeneratedPost(text="обсуждение")))
    news_service = SimpleNamespace(refresh=AsyncMock(return_value=0))
    state = BotState(telegram_id=1, interval_minutes=60)
    item = NewsItem(stable_hash="x" * 64, title="AI technology", description="new science", url="https://bbc.test/a",
                    source="BBC News", published_at=NOW, status="available")
    async with session_factory() as session:
        session.add_all([state, item]); await session.commit()
        draft = await HybridPostGenerator(generator, news_service, 100, 48, FixedRng()).generate(session, state)
        assert (draft.post_type, draft.news_source, draft.news_url) == ("news", "BBC News", item.url)
        assert item.status == "available"


@pytest.mark.asyncio
async def test_empty_news_generation_falls_back_to_regular_post(session_factory, caplog):
    generator = SimpleNamespace(generate=AsyncMock(side_effect=[
        EmptyGenerationError("empty"), GeneratedPost(text="обычный запасной пост")
    ]))
    news_service = SimpleNamespace(refresh=AsyncMock(return_value=0))
    state = BotState(telegram_id=1, interval_minutes=60)
    item = NewsItem(stable_hash="f" * 64, title="AI technology", description="new science",
                    url="https://guardian.test/a", source="The Guardian", published_at=NOW, status="available")
    async with session_factory() as session:
        session.add_all([state, item]); await session.commit()
        draft = await HybridPostGenerator(generator, news_service, 100, 48, FixedRng()).generate(session, state)
        assert draft.text == "обычный запасной пост"
        assert draft.post_type == "regular" and draft.news_item_id is None
        assert item.status == "available"
        assert generator.generate.await_count == 2
    assert "source=The Guardian" in caplog.text


@pytest.mark.asyncio
async def test_empty_regular_generation_retries_with_low_reasoning(session_factory):
    generator = SimpleNamespace(generate=AsyncMock(side_effect=[
        EmptyGenerationError("empty"), GeneratedPost(text="пост после повтора")
    ]))
    news_service = SimpleNamespace(refresh=AsyncMock(return_value=0))
    state = BotState(telegram_id=1, interval_minutes=60)
    async with session_factory() as session:
        session.add(state); await session.commit()
        draft = await HybridPostGenerator(generator, news_service, 0, 48, FixedRng()).generate(session, state)
    assert draft.text == "пост после повтора" and draft.post_type == "regular"
    retry = generator.generate.await_args_list[1]
    assert retry.kwargs == {"reasoning_effort": "low"}


@pytest.mark.asyncio
async def test_repeated_empty_generation_uses_local_emergency_post(session_factory, caplog):
    generator = SimpleNamespace(generate=AsyncMock(side_effect=EmptyGenerationError("empty")))
    news_service = SimpleNamespace(refresh=AsyncMock(return_value=0))
    state = BotState(telegram_id=1, interval_minutes=60)
    async with session_factory() as session:
        session.add(state); await session.commit()
        draft = await HybridPostGenerator(generator, news_service, 0, 48, FixedRng()).generate(session, state)
    assert draft.post_type == "regular" and draft.news_item_id is None
    assert draft.text in EMERGENCY_POSTS
    assert "using local emergency post" in caplog.text


@pytest.mark.asyncio
async def test_manual_preview_labels_type_and_separates_source_link(session_factory):
    from app.services.hybrid import GeneratedDraft
    status_message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(from_user=SimpleNamespace(id=1), message=SimpleNamespace(answer=AsyncMock(return_value=status_message)))
    workflow = SimpleNamespace(generate=AsyncMock(return_value=GeneratedDraft(
        "Текст без ссылки", "news", 7, "BBC News", "https://bbc.test/story"
    )))
    fsm = SimpleNamespace(update_data=AsyncMock())
    async with session_factory() as session:
        session.add(BotState(telegram_id=1, interval_minutes=60)); await session.commit()
        await _generate_preview(callback, session, Settings(), workflow, fsm)
    rendered = status_message.edit_text.await_args.args[0]
    assert "Новостной пост" in rendered and "BBC News" in rendered
    assert '<a href="https://bbc.test/story">' in rendered
    fsm.update_data.assert_awaited_once_with(draft="Текст без ссылки", post_type="news", news_item_id=7)


@pytest.mark.asyncio
async def test_news_marked_used_only_after_successful_publish(session_factory):
    from app.services.workflow import PublishingWorkflow
    from app.services.interfaces import PublishResult

    cipher = SimpleNamespace(decrypt=lambda value: "token", encrypt=lambda value: value)
    threads = SimpleNamespace(publish=AsyncMock(return_value=PublishResult("external")), refresh=AsyncMock())
    workflow = PublishingWorkflow(SimpleNamespace(), threads, cipher)
    state = BotState(telegram_id=1, interval_minutes=60, threads_connected=True, threads_user_id="u",
                     threads_access_token="encrypted", threads_token_expires_at=NOW + timedelta(days=30))
    first = NewsItem(stable_hash="a" * 64, title="AI one", description="", url="https://n.test/1",
                     source="BBC News", published_at=NOW, status="available")
    second = NewsItem(stable_hash="b" * 64, title="AI two", description="", url="https://n.test/2",
                      source="BBC News", published_at=NOW, status="available")
    async with session_factory() as session:
        session.add_all([state, first, second]); await session.commit()
        post = await workflow.publish_text(session, state, "ok", "news", first.id)
        assert post.status == "published" and first.status == "used"
        threads.publish.side_effect = ServiceError("failed safely")
        post = await workflow.publish_text(session, state, "fail", "news", second.id)
        assert post.status == "failed" and second.status == "available"


@pytest.mark.asyncio
async def test_guardian_secret_is_absent_from_logs(caplog):
    secret = "guardian-secret-value"
    transport = httpx.MockTransport(lambda request: httpx.Response(401, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await GuardianNewsClient(secret, client=client).fetch(48) == []
    assert secret not in caplog.text
