"""Persist discovery sources and idempotency reservations."""
from alembic import op
import sqlalchemy as sa

revision = "0003_discovery"
down_revision = "0002_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("discovery_sources", sa.Column("source_id", sa.String(255), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True), sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_discovery_sources_telegram_id", "discovery_sources", ["telegram_id"])
    op.create_index("ix_discovery_sources_text_hash", "discovery_sources", ["text_hash"])
    op.create_table("discovery_runs", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("post_id", sa.Integer()), sa.Column("diagnostic", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("telegram_id", "content_hash", name="uq_discovery_content"))
    op.create_index("ix_discovery_runs_telegram_id", "discovery_runs", ["telegram_id"])


def downgrade() -> None:
    op.drop_table("discovery_runs")
    op.drop_table("discovery_sources")
