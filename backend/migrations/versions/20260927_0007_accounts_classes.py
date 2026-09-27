"""Add local registered accounts and teacher-managed classes.

Revision ID: 20260927_0007
Revises: 20260927_0006
"""

import sqlalchemy as sa
from alembic import op

revision = "20260927_0007"
down_revision = "20260927_0006"
branch_labels = None
depends_on = None
SCHEMA = "teaching_business"


def upgrade() -> None:
    op.create_table(
        "account_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey(f"{SCHEMA}.users.id"), nullable=False, unique=True),
        sa.Column("account", sa.String(40), nullable=False, unique=True),
        sa.Column("password_salt", sa.String(32), nullable=False),
        sa.Column("password_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "class_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("teacher_id", sa.String(36), sa.ForeignKey(f"{SCHEMA}.users.id"), nullable=False),
        sa.Column("course_id", sa.String(36), sa.ForeignKey(f"{SCHEMA}.courses.id"), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("teacher_id", "name"),
        schema=SCHEMA,
    )
    op.create_index("ix_class_groups_teacher_id", "class_groups", ["teacher_id"], schema=SCHEMA)
    op.create_index("ix_class_groups_course_id", "class_groups", ["course_id"], schema=SCHEMA)
    op.create_table(
        "class_enrollments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("class_id", sa.String(36), sa.ForeignKey(f"{SCHEMA}.class_groups.id"), nullable=False),
        sa.Column("student_id", sa.String(36), sa.ForeignKey(f"{SCHEMA}.users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("class_id", "student_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_class_enrollments_class_id", "class_enrollments", ["class_id"], schema=SCHEMA)
    op.create_index("ix_class_enrollments_student_id", "class_enrollments", ["student_id"], schema=SCHEMA)
    if op.get_bind().dialect.name == "postgresql":
        for table in ("account_credentials", "class_groups", "class_enrollments"):
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {SCHEMA}.{table} TO teaching_app")


def downgrade() -> None:
    op.drop_table("class_enrollments", schema=SCHEMA)
    op.drop_table("class_groups", schema=SCHEMA)
    op.drop_table("account_credentials", schema=SCHEMA)
