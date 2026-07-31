"""Add OAuth, generation settings and publication diagnostics."""
from alembic import op
import sqlalchemy as sa

revision = "0002_integrations"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bot_states", sa.Column("threads_user_id", sa.String(255)))
    op.add_column("bot_states", sa.Column("threads_username", sa.String(255)))
    op.add_column("bot_states", sa.Column("threads_access_token", sa.Text()))
    op.add_column("bot_states", sa.Column("threads_token_expires_at", sa.DateTime(timezone=True)))
    op.add_column("bot_states", sa.Column("system_prompt", sa.Text()))
    op.add_column("bot_states", sa.Column("topic", sa.Text()))
    op.add_column("bot_states", sa.Column("last_job_started_at", sa.DateTime(timezone=True)))
    op.add_column("posts", sa.Column("diagnostic", sa.Text()))
    op.add_column("posts", sa.Column("published_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for column in ("published_at", "diagnostic"):
        op.drop_column("posts", column)
    for column in ("last_job_started_at", "topic", "system_prompt", "threads_token_expires_at", "threads_access_token", "threads_username", "threads_user_id"):
        op.drop_column("bot_states", column)
