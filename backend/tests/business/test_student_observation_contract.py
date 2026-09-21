from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy import select

from app.business.models import (
    Base,
    Course,
    CourseMembership,
    RequirementDefinition,
    RequirementResult,
    Snapshot,
    User,
)
from app.business.service import BusinessService
from app.business.stage03 import DatabaseTeachingEventStore, _LoopBridge
from app.main import create_app


@pytest.mark.asyncio
async def test_student_observation_satisfies_current_stage_explanation(tmp_path):
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'observation.db').as_posix()}",
        sqlite_test_mode=True,
        environment="test",
        dev_auth_enabled=True,
    )
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = BusinessService()
    async with app.state.session_factory() as session:
        teacher = User(id="teacher", email="teacher@observation.test", display_name="教师", system_role="teacher")
        student = User(id="student", email="student@observation.test", display_name="学生", system_role="student")
        course = Course(id="course", code="OBSERVATION", name="观察合同")
        session.add_all([teacher, student, course])
        session.add_all([
            CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
            CourseMembership(course_id=course.id, user_id=student.id, role="student"),
        ])
        await session.commit()
        version = await service.create_faq_version(session, course_id=course.id, actor_id=teacher.id)
        attempt = await service.create_attempt(session, task_version_id=version.id, learner_id=student.id, mode="guided_practice")
        snapshot = Snapshot(id="snapshot-understand", attempt_id=attempt.id, snapshot_ref="workspace://understand", sequence=1)
        session.add(snapshot)
        await session.commit()
        attempt_id = attempt.id

    store = DatabaseTeachingEventStore(
        factory=app.state.session_factory,
        service=service,
        bridge=cast(_LoopBridge, cast(Any, None)),
    )
    await store._persist(
        operation_id="observation-understand-stage",
        attempt_id=attempt_id,
        expected_state_version=0,
        event={
            "event_type": "request_guidance",
            "actor_id": "student",
            "actor_role": "student",
            "snapshot_id": "snapshot-understand",
            "student_observation": "FAQ 回答必须来自给定资料，并且每条回答都需要提供可核对的来源链接。",
        },
    )

    async with app.state.session_factory() as session:
        definition = await session.scalar(select(RequirementDefinition).where(RequirementDefinition.requirement_key == "explain_scope"))
        result = await session.scalar(select(RequirementResult).where(RequirementResult.requirement_id == definition.id))
        assert result is not None
        assert result.status == "SATISFIED"
        assert result.snapshot_id == "snapshot-understand"
        assert result.evaluator == "server:student_explanation:v1"
    await app.state.business_engine.dispose()
