from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from . import BUSINESS_SCHEMA


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = ( {"schema": BUSINESS_SCHEMA}, )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    system_role: Mapped[str] = mapped_column(String(24), nullable=False, default="student")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = ( {"schema": BUSINESS_SCHEMA}, )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CourseMembership(Base):
    __tablename__ = "course_memberships"
    __table_args__ = (UniqueConstraint("course_id", "user_id"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.courses.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.users.id"), index=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("course_id", "task_key"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.courses.id"), index=True)
    task_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class TaskVersion(Base):
    __tablename__ = "task_versions"
    __table_args__ = (UniqueConstraint("task_id", "version"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.tasks.id"), index=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="published")
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskStage(Base):
    __tablename__ = "task_stages"
    __table_args__ = (UniqueConstraint("task_version_id", "stage_key"), UniqueConstraint("task_version_id", "position"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_version_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.task_versions.id"), index=True)
    stage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregation: Mapped[str] = mapped_column(String(24), default="ALL_REQUIRED")


class RequirementDefinition(Base):
    __tablename__ = "requirement_definitions"
    __table_args__ = (UniqueConstraint("task_stage_id", "requirement_key", "version"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_stage_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.task_stages.id"), index=True)
    requirement_key: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    evaluator: Mapped[str] = mapped_column(String(120), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = ( {"schema": BUSINESS_SCHEMA}, )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_version_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.task_versions.id"), index=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.users.id"), index=True)
    current_stage_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.task_stages.id"))
    mode: Mapped[str] = mapped_column(String(32), default="guided_practice")
    status: Mapped[str] = mapped_column(String(32), default="active")
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    student_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    infrastructure_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("attempt_id", "snapshot_ref"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.attempts.id"), index=True)
    snapshot_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TeachingEvent(Base):
    __tablename__ = "teaching_events"
    __table_args__ = (UniqueConstraint("attempt_id", "operation_id"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.attempts.id"), index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RequirementResult(Base):
    __tablename__ = "requirement_results"
    __table_args__ = (UniqueConstraint("attempt_id", "requirement_id", "version"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.attempts.id"), index=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.requirement_definitions.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    evaluator: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class Intervention(Base):
    __tablename__ = "interventions"
    __table_args__ = (UniqueConstraint("operation_id"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.attempts.id"), index=True)
    operation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    requested_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    assigned_teacher_id: Mapped[str | None] = mapped_column(ForeignKey(f"{BUSINESS_SCHEMA}.users.id"), nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    allow_l2: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperationLedger(Base):
    __tablename__ = "operation_ledger"
    __table_args__ = (UniqueConstraint("scope", "operation_id"), {"schema": BUSINESS_SCHEMA})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(80), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="completed")
    result_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    result_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
