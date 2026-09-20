"""Rename the FAQ delivery rubric to match its individual-task evidence.

Revision ID: 20260921_0005
Revises: 20260920_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20260921_0005"
down_revision = "20260920_0004"
branch_labels = None
depends_on = None
SCHEMA = "teaching_business"


def upgrade() -> None:
    rubric = sa.table(
        "rubric_definitions",
        sa.column("item_key", sa.String()),
        sa.column("title", sa.String()),
        schema=SCHEMA,
    )
    op.execute(
        rubric.update()
        .where(rubric.c.item_key == "delivery_collab")
        .where(rubric.c.title == "规范与协作")
        .values(title="工程规范与可复现性")
    )


def downgrade() -> None:
    rubric = sa.table(
        "rubric_definitions",
        sa.column("item_key", sa.String()),
        sa.column("title", sa.String()),
        schema=SCHEMA,
    )
    op.execute(
        rubric.update()
        .where(rubric.c.item_key == "delivery_collab")
        .where(rubric.c.title == "工程规范与可复现性")
        .values(title="规范与协作")
    )
