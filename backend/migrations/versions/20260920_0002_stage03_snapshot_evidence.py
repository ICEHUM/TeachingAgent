"""Bind requirement results to immutable snapshots and tool operations.

Revision ID: 20260920_0002
Revises: 20260920_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20260920_0002"
down_revision = "20260920_0001"
branch_labels = None
depends_on = None
SCHEMA = "teaching_business"


def upgrade() -> None:
    op.add_column(
        "requirement_results",
        sa.Column("snapshot_id", sa.String(36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "requirement_results",
        sa.Column("operation_id", sa.String(160), nullable=True),
        schema=SCHEMA,
    )
    # Preserve Stage 02A rows by binding them to the latest existing snapshot.
    # A deterministic legacy snapshot is created only when an attempt had results
    # but no snapshot fact.
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA}.snapshots (id, attempt_id, snapshot_ref, sequence, created_at)
            SELECT
              substr(md5(rr.attempt_id || '-stage03-legacy'), 1, 8) || '-' ||
              substr(md5(rr.attempt_id || '-stage03-legacy'), 9, 4) || '-' ||
              substr(md5(rr.attempt_id || '-stage03-legacy'), 13, 4) || '-' ||
              substr(md5(rr.attempt_id || '-stage03-legacy'), 17, 4) || '-' ||
              substr(md5(rr.attempt_id || '-stage03-legacy'), 21, 12),
              rr.attempt_id,
              'legacy://stage02/' || rr.attempt_id,
              0,
              CURRENT_TIMESTAMP
            FROM (SELECT DISTINCT attempt_id FROM {SCHEMA}.requirement_results) rr
            WHERE NOT EXISTS (
              SELECT 1 FROM {SCHEMA}.snapshots s WHERE s.attempt_id = rr.attempt_id
            )
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.requirement_results rr
            SET snapshot_id = (
              SELECT s.id
              FROM {SCHEMA}.snapshots s
              WHERE s.attempt_id = rr.attempt_id
              ORDER BY s.sequence DESC
              LIMIT 1
            ),
                operation_id = 'legacy-' || rr.id
            """
        )
    )
    op.alter_column("requirement_results", "snapshot_id", nullable=False, schema=SCHEMA)
    op.alter_column("requirement_results", "operation_id", nullable=False, schema=SCHEMA)
    op.drop_constraint(
        "uq_results_attempt_requirement_version",
        "requirement_results",
        type_="unique",
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_results_snapshot",
        "requirement_results",
        "snapshots",
        ["snapshot_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_results_attempt_operation",
        "requirement_results",
        ["attempt_id", "operation_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_teaching_business_requirement_results_snapshot_id",
        "requirement_results",
        ["snapshot_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_teaching_business_requirement_results_operation_id",
        "requirement_results",
        ["operation_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_results_attempt_requirement_snapshot_evaluated",
        "requirement_results",
        ["attempt_id", "requirement_id", "snapshot_id", "evaluated_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Stage 02A stored one result per attempt/requirement/version. Keep the newest
    # row when rolling back from Stage 03 history.
    op.execute(
        sa.text(
            f"""
            DELETE FROM {SCHEMA}.requirement_results older
            USING {SCHEMA}.requirement_results newer
            WHERE older.attempt_id = newer.attempt_id
              AND older.requirement_id = newer.requirement_id
              AND older.version = newer.version
              AND (older.evaluated_at, older.id) < (newer.evaluated_at, newer.id)
            """
        )
    )
    op.drop_index(
        "ix_results_attempt_requirement_snapshot_evaluated",
        table_name="requirement_results",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_teaching_business_requirement_results_operation_id",
        table_name="requirement_results",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_teaching_business_requirement_results_snapshot_id",
        table_name="requirement_results",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_results_attempt_operation",
        "requirement_results",
        type_="unique",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "fk_results_snapshot",
        "requirement_results",
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_results_attempt_requirement_version",
        "requirement_results",
        ["attempt_id", "requirement_id", "version"],
        schema=SCHEMA,
    )
    op.drop_column("requirement_results", "operation_id", schema=SCHEMA)
    op.drop_column("requirement_results", "snapshot_id", schema=SCHEMA)
