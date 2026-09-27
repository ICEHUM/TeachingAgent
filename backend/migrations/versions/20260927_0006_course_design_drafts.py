"""Store teacher course designs and AI question drafts.

Revision ID: 20260927_0006
Revises: 20260921_0005
"""

import sqlalchemy as sa
from alembic import op

revision = "20260927_0006"
down_revision = "20260921_0005"
branch_labels = None
depends_on = None
SCHEMA = "teaching_business"


def upgrade() -> None:
    op.create_table(
        "course_designs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(36), sa.ForeignKey(f"{SCHEMA}.courses.id"), nullable=False),
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey(f"{SCHEMA}.users.id"), nullable=False),
        sa.Column("file_name", sa.String(240), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("file_content", sa.LargeBinary(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_course_designs_course_id", "course_designs", ["course_id"], schema=SCHEMA)
    op.create_table(
        "question_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(36), sa.ForeignKey(f"{SCHEMA}.courses.id"), nullable=False),
        sa.Column("source_design_id", sa.String(36), sa.ForeignKey(f"{SCHEMA}.course_designs.id"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey(f"{SCHEMA}.users.id"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("starter_code", sa.Text(), nullable=False),
        sa.Column("sample_input", sa.Text(), nullable=False),
        sa.Column("sample_output", sa.Text(), nullable=False),
        sa.Column("answer_outline", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_question_drafts_course_id", "question_drafts", ["course_id"], schema=SCHEMA)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON teaching_business.course_designs TO teaching_app")
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON teaching_business.question_drafts TO teaching_app")


def downgrade() -> None:
    op.drop_table("question_drafts", schema=SCHEMA)
    op.drop_table("course_designs", schema=SCHEMA)
