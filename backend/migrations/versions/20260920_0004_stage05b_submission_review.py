"""Add submission, rubric, review, and formal grade tables.

Revision ID: 20260920_0004
Revises: 20260920_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "20260920_0004"
down_revision = "20260920_0003"
branch_labels = None
depends_on = None
SCHEMA = "teaching_business"


def upgrade() -> None:
    op.create_table(
        "rubric_definitions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_version_id", sa.String(36), nullable=False),
        sa.Column("item_key", sa.String(80), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("max_score", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("requirement_keys", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["task_version_id"], [f"{SCHEMA}.task_versions.id"], name="fk_rubric_task_version"),
        sa.PrimaryKeyConstraint("id", name="pk_rubric_definitions"),
        schema=SCHEMA,
    )
    op.create_unique_constraint("uq_rubric_version_key", "rubric_definitions", ["task_version_id", "item_key"], schema=SCHEMA)
    op.create_index("ix_rubric_task_version_id", "rubric_definitions", ["task_version_id"], schema=SCHEMA)

    op.create_table(
        "submissions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String(160), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("assistance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], [f"{SCHEMA}.attempts.id"], name="fk_submissions_attempt"),
        sa.ForeignKeyConstraint(["snapshot_id"], [f"{SCHEMA}.snapshots.id"], name="fk_submissions_snapshot"),
        sa.PrimaryKeyConstraint("id", name="pk_submissions"),
        schema=SCHEMA,
    )
    op.create_unique_constraint("uq_submissions_attempt_operation", "submissions", ["attempt_id", "operation_id"], schema=SCHEMA)
    op.create_unique_constraint("uq_submissions_attempt_snapshot", "submissions", ["attempt_id", "snapshot_id"], schema=SCHEMA)
    op.create_index("ix_submissions_attempt_id", "submissions", ["attempt_id"], schema=SCHEMA)
    op.create_index("ix_submissions_snapshot_id", "submissions", ["snapshot_id"], schema=SCHEMA)

    op.create_table(
        "reviews",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("submission_id", sa.String(36), nullable=False),
        sa.Column("teacher_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("publish_operation_id", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["submission_id"], [f"{SCHEMA}.submissions.id"], name="fk_reviews_submission"),
        sa.ForeignKeyConstraint(["teacher_id"], [f"{SCHEMA}.users.id"], name="fk_reviews_teacher"),
        sa.PrimaryKeyConstraint("id", name="pk_reviews"),
        schema=SCHEMA,
    )
    op.create_unique_constraint("uq_reviews_submission", "reviews", ["submission_id"], schema=SCHEMA)
    op.create_index("ix_reviews_submission_id", "reviews", ["submission_id"], schema=SCHEMA)
    op.create_index("ix_reviews_status", "reviews", ["status"], schema=SCHEMA)

    op.create_table(
        "review_items",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("review_id", sa.String(36), nullable=False),
        sa.Column("rubric_key", sa.String(80), nullable=False),
        sa.Column("auto_status", sa.String(24), nullable=False),
        sa.Column("auto_summary", sa.Text(), nullable=False),
        sa.Column("ai_text", sa.Text(), nullable=False),
        sa.Column("ai_evidence_refs", sa.JSON(), nullable=False),
        sa.Column("teacher_score", sa.Integer(), nullable=True),
        sa.Column("teacher_reason", sa.Text(), nullable=False),
        sa.Column("teacher_confirmed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], [f"{SCHEMA}.reviews.id"], name="fk_review_items_review"),
        sa.PrimaryKeyConstraint("id", name="pk_review_items"),
        schema=SCHEMA,
    )
    op.create_unique_constraint("uq_review_items_key", "review_items", ["review_id", "rubric_key"], schema=SCHEMA)
    op.create_index("ix_review_items_review_id", "review_items", ["review_id"], schema=SCHEMA)

    op.create_table(
        "formal_grades",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("submission_id", sa.String(36), nullable=False),
        sa.Column("review_id", sa.String(36), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("max_score", sa.Integer(), nullable=False),
        sa.Column("published_by", sa.String(36), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], [f"{SCHEMA}.submissions.id"], name="fk_grades_submission"),
        sa.ForeignKeyConstraint(["review_id"], [f"{SCHEMA}.reviews.id"], name="fk_grades_review"),
        sa.ForeignKeyConstraint(["published_by"], [f"{SCHEMA}.users.id"], name="fk_grades_publisher"),
        sa.PrimaryKeyConstraint("id", name="pk_formal_grades"),
        schema=SCHEMA,
    )
    op.create_unique_constraint("uq_formal_grades_submission", "formal_grades", ["submission_id"], schema=SCHEMA)
    op.create_index("ix_formal_grades_submission_id", "formal_grades", ["submission_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("formal_grades", schema=SCHEMA)
    op.drop_table("review_items", schema=SCHEMA)
    op.drop_table("reviews", schema=SCHEMA)
    op.drop_table("submissions", schema=SCHEMA)
    op.drop_table("rubric_definitions", schema=SCHEMA)
