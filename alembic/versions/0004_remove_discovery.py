"""Remove the discontinued Threads discovery storage."""

from alembic import op
import sqlalchemy as sa

revision = "0004_remove_discovery"
down_revision = "0003_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("discovery_runs")
    op.drop_table("discovery_sources")


def downgrade() -> None:
    op.create_table(
        "discovery_sources",
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("source_id", "telegram_id"),
    )
    op.create_index("ix_discovery_sources_telegram_id", "discovery_sources", ["telegram_id"])
    op.create_index("ix_discovery_sources_text_hash", "discovery_sources", ["text_hash"])
    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=True),
        sa.Column("diagnostic", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id", "content_hash", name="uq_discovery_content"),
    )
    op.create_index("ix_discovery_runs_telegram_id", "discovery_runs", ["telegram_id"])
