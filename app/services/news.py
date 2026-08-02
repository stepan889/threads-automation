import asyncio
import hashlib
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsItem

logger = logging.getLogger(__name__)

BBC_FEEDS = {
    "World": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "Technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "Science": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "Sport": "https://feeds.bbci.co.uk/sport/rss.xml",
}
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    return _SPACE.sub(" ", html.unescape(_TAGS.sub(" ", value or ""))).strip()


def stable_hash(title: str) -> str:
    normalized = re.sub(r"[^\w\s]", "", clean_text(title).lower())
    return hashlib.sha256(_SPACE.sub(" ", normalized).encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class NewsRecord:
    stable_hash: str
    title: str
    description: str
    url: str
    source: str
    published_at: datetime


def parse_bbc_rss(payload: str | bytes) -> list[NewsRecord]:
    root = ElementTree.fromstring(payload)
    records = []
    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title"))
        url = clean_text(item.findtext("link"))
        published = clean_text(item.findtext("pubDate"))
        if not title or not url or not published:
            continue
        try:
            published_at = _utc(parsedate_to_datetime(published))
        except (TypeError, ValueError):
            continue
        records.append(NewsRecord(stable_hash(title), title, clean_text(item.findtext("description")), url, "BBC News", published_at))
    return records


def parse_guardian_response(data: dict) -> list[NewsRecord]:
    records = []
    for item in data.get("response", {}).get("results", []):
        title, url, published = clean_text(item.get("webTitle")), clean_text(item.get("webUrl")), item.get("webPublicationDate")
        if not title or not url or not published:
            continue
        try:
            published_at = _utc(datetime.fromisoformat(str(published).replace("Z", "+00:00")))
        except ValueError:
            continue
        fields = item.get("fields") or {}
        description = clean_text(fields.get("trailText") or fields.get("standfirst"))
        records.append(NewsRecord(stable_hash(title), title, description, url, "The Guardian", published_at))
    return records


class BBCNewsClient:
    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 10) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def fetch(self) -> list[NewsRecord]:
        records = []
        for category, url in BBC_FEEDS.items():
            try:
                response = await _get_with_retry(self._client, url)
                records.extend(parse_bbc_rss(response.content))
            except (httpx.HTTPError, ElementTree.ParseError, ValueError) as exc:
                logger.warning("BBC news feed unavailable category=%s error=%s", category, type(exc).__name__)
        return records

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class GuardianNewsClient:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None, timeout: float = 10) -> None:
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def fetch(self, lookback_hours: int) -> list[NewsRecord]:
        if not self.api_key:
            return []
        since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        try:
            response = await _get_with_retry(self._client, "https://content.guardianapis.com/search", params={
                "api-key": self.api_key, "from-date": since.date().isoformat(), "order-by": "newest",
                "page-size": 50, "show-fields": "trailText,standfirst",
                "q": "AI OR neural network OR technology OR startup OR business OR science OR analytics OR sport",
            })
            return parse_guardian_response(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Guardian news API unavailable error=%s", type(exc).__name__)
            return []

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class NewsFilter:
    _aliases = {
        "искусственный интеллект": ("artificial intelligence", " ai ", "machine learning"),
        "нейросети": ("neural network", "neural model", "generative ai"),
        "аналитика": ("analytics", "data analysis", "insight"),
        "спорт": ("sport", "football", "tennis", "basketball", "hockey", "athlete"),
        "технологии": ("technology", "tech"), "интернет-продукты": ("internet", "app", "platform"),
        "стартапы": ("startup",), "бизнес": ("business", "company"),
        "наука": ("science", "research"), "инновации": ("innovation",),
        "финансы": ("finance", "financial", "market"), "маркетинг": ("marketing", "advertising"),
        "образование": ("education", "learning"), "продуктивность": ("productivity", "workplace"),
        "культура": ("culture", "art", "film", "music"), "мировые события": ("world", "global"),
    }
    _safety_english = ("war", "killed", "death", "dead", "terror", "disaster", "shooting", "explosion", "attack")

    def __init__(self, topics: tuple[str, ...], blocked_phrases: tuple[str, ...], lookback_hours: int) -> None:
        self.topics, self.blocked = topics, blocked_phrases
        self.lookback_hours = lookback_hours

    def accepts(self, item: NewsRecord, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if item.published_at < now - timedelta(hours=self.lookback_hours) or item.published_at > now + timedelta(minutes=10):
            return False
        text = f" {item.title} {item.description} ".lower()
        if any(phrase in text for phrase in self.blocked):
            return False
        if any(re.search(rf"\b{re.escape(phrase)}\b", text) for phrase in self._safety_english):
            return False
        terms = list(self.topics)
        for topic in self.topics:
            terms.extend(self._aliases.get(topic, ()))
        return any(topic in text for topic in terms)


class NewsService:
    def __init__(self, bbc: BBCNewsClient, guardian: GuardianNewsClient, news_filter: NewsFilter) -> None:
        self.bbc, self.guardian, self.news_filter = bbc, guardian, news_filter

    async def refresh(self, session: AsyncSession) -> int:
        bbc, guardian = await asyncio.gather(
            self.bbc.fetch(), self.guardian.fetch(self.news_filter.lookback_hours), return_exceptions=True
        )
        incoming = []
        for result, source in ((bbc, "BBC"), (guardian, "Guardian")):
            if isinstance(result, Exception):
                logger.warning("News source unavailable source=%s error=%s", source, type(result).__name__)
            else:
                incoming.extend(result)
        accepted = [item for item in incoming if self.news_filter.accepts(item)]
        existing = list(await session.scalars(select(NewsItem).where(or_(
            NewsItem.url.in_([x.url for x in accepted]), NewsItem.stable_hash.in_([x.stable_hash for x in accepted])
        )))) if accepted else []
        urls, hashes = {x.url for x in existing}, {x.stable_hash for x in existing}
        known_titles = [clean_text(x.title).lower() for x in existing]
        added = 0
        for item in sorted(accepted, key=lambda x: x.published_at):
            normalized = clean_text(item.title).lower()
            if item.url in urls or item.stable_hash in hashes or any(SequenceMatcher(None, normalized, title).ratio() >= .86 for title in known_titles):
                continue
            session.add(NewsItem(**item.__dict__))
            urls.add(item.url); hashes.add(item.stable_hash); known_titles.append(normalized); added += 1
        if added:
            await session.commit()
        return added

    async def close(self) -> None:
        await self.bbc.close(); await self.guardian.close()


async def _get_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """Retry idempotent news reads once; never includes URL/query details in logs."""
    last_error = None
    for attempt in range(2):
        try:
            response = await client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_error = exc
            retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
            if attempt == 0 and retryable:
                await asyncio.sleep(.1)
                continue
            raise
    raise last_error  # pragma: no cover
