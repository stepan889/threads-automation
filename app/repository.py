from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotState, NewsItem, Post


async def running_states(session: AsyncSession) -> list[BotState]:
    return list(await session.scalars(select(BotState).where(BotState.is_running.is_(True))))


async def get_or_create_state(
    session: AsyncSession, telegram_id: int, default_interval: int
) -> BotState:
    state = await session.get(BotState, telegram_id)
    if state is None:
        state = BotState(telegram_id=telegram_id, interval_minutes=default_interval)
        session.add(state)
        await session.commit()
        await session.refresh(state)
    return state


async def recent_posts(session: AsyncSession, telegram_id: int, limit: int = 5) -> list[Post]:
    result = await session.scalars(
        select(Post)
        .where(Post.telegram_id == telegram_id)
        .order_by(desc(Post.created_at))
        .limit(limit)
    )
    return list(result)


async def last_published_post(session: AsyncSession, telegram_id: int) -> Post | None:
    return await session.scalar(
        select(Post).where(Post.telegram_id == telegram_id, Post.status == "published")
        .order_by(desc(Post.published_at), desc(Post.id)).limit(1)
    )


async def available_news(session: AsyncSession, since, limit: int = 50) -> list[NewsItem]:
    result = await session.scalars(
        select(NewsItem).where(NewsItem.status == "available", NewsItem.published_at >= since)
        .order_by(desc(NewsItem.published_at)).limit(limit)
    )
    return list(result)
