from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BotState(Base):
    __tablename__ = "bot_states"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    threads_connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    threads_user_id: Mapped[str | None] = mapped_column(String(255))
    threads_username: Mapped[str | None] = mapped_column(String(255))
    threads_access_token: Mapped[str | None] = mapped_column(Text)
    threads_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)
    last_job_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    diagnostic: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
