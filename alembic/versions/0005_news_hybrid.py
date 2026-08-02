"""Add news items and post provenance."""

from alembic import op
import sqlalchemy as sa

revision = "0005_news_hybrid"
down_revision = "0004_remove_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stable_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="available", nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("stable_hash"), sa.UniqueConstraint("url"),
    )
    op.create_index("ix_news_items_stable_hash", "news_items", ["stable_hash"])
    op.create_index("ix_news_items_published_at", "news_items", ["published_at"])
    op.create_index("ix_news_items_status", "news_items", ["status"])
    op.add_column("posts", sa.Column("post_type", sa.String(length=16), server_default="regular", nullable=False))
    op.add_column("posts", sa.Column("news_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_posts_news_item", "posts", "news_items", ["news_item_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_posts_news_item", "posts", type_="foreignkey")
    op.drop_column("posts", "news_item_id")
    op.drop_column("posts", "post_type")
    op.drop_table("news_items")
