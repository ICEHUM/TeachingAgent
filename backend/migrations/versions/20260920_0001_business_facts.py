"""Create stage 02A authoritative business facts with explicit DDL.

Revision ID: 20260920_0001
Revises: None
"""

import sqlalchemy as sa
from alembic import op

revision = "20260920_0001"
down_revision = None
branch_labels = None
depends_on = None
SCHEMA = "teaching_business"


def upgrade() -> None:
    # Schema creation belongs to deploy/bootstrap-postgres.sql.
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("system_role", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_users_email", "users", ["email"], schema=SCHEMA)

    op.create_table(
        "courses",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_courses"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_courses_code", "courses", ["code"], schema=SCHEMA)

    op.create_table(
        "course_memberships",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], [f"{SCHEMA}.courses.id"], name="fk_memberships_course"),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], name="fk_memberships_user"),
        sa.PrimaryKeyConstraint("id", name="pk_course_memberships"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_memberships_course_user", "course_memberships", ["course_id", "user_id"], schema=SCHEMA)
    op.create_index("ix_teaching_business_course_memberships_course_id", "course_memberships", ["course_id"], schema=SCHEMA)
    op.create_index("ix_teaching_business_course_memberships_user_id", "course_memberships", ["user_id"], schema=SCHEMA)

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("task_key", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], [f"{SCHEMA}.courses.id"], name="fk_tasks_course"),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_tasks_course_key", "tasks", ["course_id", "task_key"], schema=SCHEMA)
    op.create_index("ix_teaching_business_tasks_course_id", "tasks", ["course_id"], schema=SCHEMA)

    op.create_table(
        "task_versions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], [f"{SCHEMA}.tasks.id"], name="fk_versions_task"),
        sa.PrimaryKeyConstraint("id", name="pk_task_versions"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_task_versions_task_version", "task_versions", ["task_id", "version"], schema=SCHEMA)
    op.create_index("ix_teaching_business_task_versions_task_id", "task_versions", ["task_id"], schema=SCHEMA)

    op.create_table(
        "task_stages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_version_id", sa.String(36), nullable=False),
        sa.Column("stage_key", sa.String(80), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("aggregation", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["task_version_id"], [f"{SCHEMA}.task_versions.id"], name="fk_stages_task_version"),
        sa.PrimaryKeyConstraint("id", name="pk_task_stages"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_stages_version_key", "task_stages", ["task_version_id", "stage_key"], schema=SCHEMA)
    op.create_unique_constraint("uq_stages_version_position", "task_stages", ["task_version_id", "position"], schema=SCHEMA)
    op.create_index("ix_teaching_business_task_stages_task_version_id", "task_stages", ["task_version_id"], schema=SCHEMA)

    op.create_table(
        "requirement_definitions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_stage_id", sa.String(36), nullable=False),
        sa.Column("requirement_key", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("evaluator", sa.String(120), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["task_stage_id"], [f"{SCHEMA}.task_stages.id"], name="fk_requirements_stage"),
        sa.PrimaryKeyConstraint("id", name="pk_requirement_definitions"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_requirements_stage_key_version", "requirement_definitions", ["task_stage_id", "requirement_key", "version"], schema=SCHEMA)
    op.create_index("ix_teaching_business_requirement_definitions_task_stage_id", "requirement_definitions", ["task_stage_id"], schema=SCHEMA)

    op.create_table(
        "attempts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_version_id", sa.String(36), nullable=False),
        sa.Column("learner_id", sa.String(36), nullable=False),
        sa.Column("current_stage_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("student_failure_count", sa.Integer(), nullable=False),
        sa.Column("infrastructure_failure_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_version_id"], [f"{SCHEMA}.task_versions.id"], name="fk_attempts_task_version"),
        sa.ForeignKeyConstraint(["learner_id"], [f"{SCHEMA}.users.id"], name="fk_attempts_learner"),
        sa.ForeignKeyConstraint(["current_stage_id"], [f"{SCHEMA}.task_stages.id"], name="fk_attempts_stage"),
        sa.PrimaryKeyConstraint("id", name="pk_attempts"), schema=SCHEMA,
    )
    op.create_index("ix_teaching_business_attempts_task_version_id", "attempts", ["task_version_id"], schema=SCHEMA)
    op.create_index("ix_teaching_business_attempts_learner_id", "attempts", ["learner_id"], schema=SCHEMA)

    op.create_table(
        "snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("snapshot_ref", sa.String(300), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], [f"{SCHEMA}.attempts.id"], name="fk_snapshots_attempt"),
        sa.PrimaryKeyConstraint("id", name="pk_snapshots"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_snapshots_attempt_ref", "snapshots", ["attempt_id", "snapshot_ref"], schema=SCHEMA)
    op.create_index("ix_teaching_business_snapshots_attempt_id", "snapshots", ["attempt_id"], schema=SCHEMA)

    op.create_table(
        "teaching_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("operation_id", sa.String(160), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], [f"{SCHEMA}.attempts.id"], name="fk_events_attempt"),
        sa.ForeignKeyConstraint(["actor_id"], [f"{SCHEMA}.users.id"], name="fk_events_actor"),
        sa.PrimaryKeyConstraint("id", name="pk_teaching_events"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_events_attempt_operation", "teaching_events", ["attempt_id", "operation_id"], schema=SCHEMA)
    op.create_index("ix_teaching_business_teaching_events_attempt_id", "teaching_events", ["attempt_id"], schema=SCHEMA)
    op.create_index("ix_teaching_business_teaching_events_created_at", "teaching_events", ["created_at"], schema=SCHEMA)

    op.create_table(
        "requirement_results",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("requirement_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("evaluator", sa.String(120), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], [f"{SCHEMA}.attempts.id"], name="fk_results_attempt"),
        sa.ForeignKeyConstraint(["requirement_id"], [f"{SCHEMA}.requirement_definitions.id"], name="fk_results_requirement"),
        sa.PrimaryKeyConstraint("id", name="pk_requirement_results"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_results_attempt_requirement_version", "requirement_results", ["attempt_id", "requirement_id", "version"], schema=SCHEMA)
    op.create_index("ix_teaching_business_requirement_results_attempt_id", "requirement_results", ["attempt_id"], schema=SCHEMA)
    op.create_index("ix_teaching_business_requirement_results_requirement_id", "requirement_results", ["requirement_id"], schema=SCHEMA)

    op.create_table(
        "interventions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_state_version", sa.Integer(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("assigned_teacher_id", sa.String(36), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("allow_l2", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["attempt_id"], [f"{SCHEMA}.attempts.id"], name="fk_interventions_attempt"),
        sa.ForeignKeyConstraint(["assigned_teacher_id"], [f"{SCHEMA}.users.id"], name="fk_interventions_teacher"),
        sa.PrimaryKeyConstraint("id", name="pk_interventions"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_interventions_operation", "interventions", ["operation_id"], schema=SCHEMA)
    op.create_index("ix_teaching_business_interventions_attempt_id", "interventions", ["attempt_id"], schema=SCHEMA)
    op.create_index("ix_teaching_business_interventions_status", "interventions", ["status"], schema=SCHEMA)

    op.create_table(
        "operation_ledger",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(80), nullable=False),
        sa.Column("operation_id", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result_ref", sa.String(300), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_operation_ledger"), schema=SCHEMA,
    )
    op.create_unique_constraint("uq_ledger_scope_operation", "operation_ledger", ["scope", "operation_id"], schema=SCHEMA)


def downgrade() -> None:
    # Drop only business objects. LangGraph checkpoint objects are never referenced.
    for table in (
        "operation_ledger", "interventions", "requirement_results", "teaching_events",
        "snapshots", "attempts", "requirement_definitions", "task_stages",
        "task_versions", "tasks", "course_memberships", "courses", "users",
    ):
        op.drop_table(table, schema=SCHEMA)
