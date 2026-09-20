from __future__ import annotations

import pytest
from sqlalchemy import select

from app.business.database import create_business_engine, create_session_factory
from app.business.models import (
    Base,
    Course,
    CourseMembership,
    RequirementDefinition,
    RequirementResult,
    Snapshot,
    User,
)
from app.business.service import BusinessRuleError, BusinessService


async def seeded_database():
    engine = create_business_engine("sqlite+aiosqlite:///:memory:", sqlite_test_mode=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    service = BusinessService()
    async with factory() as session:
        teacher = User(id="teacher", email="teacher@stage03.test", display_name="教师", system_role="teacher")
        student = User(id="student", email="student@stage03.test", display_name="学生")
        course = Course(id="course", code="STAGE03", name="Stage 03")
        session.add_all([teacher, student, course])
        session.add_all([
            CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
            CourseMembership(course_id=course.id, user_id=student.id, role="student"),
        ])
        await session.commit()
        version = await service.create_faq_version(
            session, course_id=course.id, actor_id=teacher.id
        )
        attempt = await service.create_attempt(
            session,
            task_version_id=version.id,
            learner_id=student.id,
            mode="guided_practice",
        )
        session.add_all([
            Snapshot(id="snapshot-old", attempt_id=attempt.id, snapshot_ref="workspace://old", sequence=1),
            Snapshot(id="snapshot-new", attempt_id=attempt.id, snapshot_ref="workspace://new", sequence=2),
        ])
        await session.commit()
    return engine, factory, service, attempt


@pytest.mark.asyncio
async def test_old_snapshot_cannot_update_current_requirement_and_remains_traceable():
    engine, factory, service, attempt = await seeded_database()
    async with factory() as session:
        current = await session.get(type(attempt), attempt.id)
        definition = await session.scalar(
            select(RequirementDefinition).where(
                RequirementDefinition.task_stage_id == current.current_stage_id
            )
        )
        with pytest.raises(BusinessRuleError, match="stale_snapshot"):
            await service.upsert_requirement_result(
                session,
                attempt=current,
                requirement_id=definition.id,
                snapshot_id="snapshot-old",
                operation_id="old-operation",
                status="SATISFIED",
                evaluator="openhands:test",
                evidence_refs=["artifact://old"],
                version=definition.version,
            )
        accepted = await service.upsert_requirement_result(
            session,
            attempt=current,
            requirement_id=definition.id,
            snapshot_id="snapshot-new",
            operation_id="new-operation",
            status="SATISFIED",
            evaluator="openhands:test",
            evidence_refs=["artifact://new"],
            version=definition.version,
        )
        replay = await service.upsert_requirement_result(
            session,
            attempt=current,
            requirement_id=definition.id,
            snapshot_id="snapshot-new",
            operation_id="new-operation",
            status="NOT_SATISFIED",
            evaluator="forged",
            evidence_refs=[],
            version=definition.version,
        )
        assert replay.id == accepted.id
        assert replay.status == "SATISFIED"
        newer = await service.upsert_requirement_result(
            session,
            attempt=current,
            requirement_id=definition.id,
            snapshot_id="snapshot-new",
            operation_id="newer-operation",
            status="NOT_SATISFIED",
            evaluator="openhands:test",
            evidence_refs=["artifact://newer"],
            version=definition.version,
        )
        assert newer.id != accepted.id
        assert accepted.status == "SATISFIED"
        rows = list(
            (
                await session.scalars(
                    select(RequirementResult).where(
                        RequirementResult.attempt_id == attempt.id
                    )
                )
            ).all()
        )
        assert len(rows) == 2
        aggregate = await service.evaluator.evaluate(session, attempt_id=attempt.id)
        assert aggregate.satisfied is False  # another required definition is still missing
    await engine.dispose()
