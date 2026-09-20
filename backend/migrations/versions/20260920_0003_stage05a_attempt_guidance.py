"""Add per-attempt teacher control for AI guidance.

Revision ID: 20260920_0003
Revises: 20260920_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "20260920_0003"
down_revision = "20260920_0002"
branch_labels = None
depends_on = None
SCHEMA = "teaching_business"


def upgrade() -> None:
    op.add_column(
        "attempts",
        sa.Column(
            "ai_guidance_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("attempts", "ai_guidance_paused", schema=SCHEMA)
