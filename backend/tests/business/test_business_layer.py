from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, select

from app.business.database import create_business_engine, create_session_factory
from app.business.models import (
    Base,
    Course,
    CourseMembership,
    Intervention,
    OperationLedger,
    RequirementDefinition,
    TeachingEvent,
    User,
)
from app.business.service import BusinessRuleError, BusinessService


async def make_database(url: str = "sqlite+aiosqlite:///:memory:"):
    engine = create_business_engine(url, sqlite_test_mode=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, create_session_factory(engine)


async def seed(factory):
    service = BusinessService()
    async with factory() as session:
        teacher = User(id="teacher-1", email="teacher@example.test", display_name="教师", system_role="teacher")
        outsider = User(id="teacher-2", email="outsider@example.test", display_name="外部教师", system_role="teacher")
        student1 = User(id="student-1", email="s1@example.test", display_name="学生一")
        student2 = User(id="student-2", email="s2@example.test", display_name="学生二")
        course = Course(id="course-1", code="AI-APP", name="AI应用开发实训")
        session.add_all([teacher, outsider, student1, student2, course])
        session.add_all([
            CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
            CourseMembership(course_id=course.id, user_id=student1.id, role="student"),
            CourseMembership(course_id=course.id, user_id=student2.id, role="student"),
        ])
        await session.commit()
        version = await service.create_faq_version(session, course_id=course.id, actor_id=teacher.id)
        attempt1 = await service.create_attempt(session, task_version_id=version.id, learner_id=student1.id, mode="guided_practice")
        attempt2 = await service.create_attempt(session, task_version_id=version.id, learner_id=student2.id, mode="guided_practice")
        return service, version, attempt1, attempt2


@pytest.mark.asyncio
async def test_requirement_results_are_server_aggregated_and_client_flag_has_no_authority():
    engine, factory = await make_database()
    service, _, attempt, _ = await seed(factory)
    async with factory() as session:
        current = await session.get(type(attempt), attempt.id)
        event, _ = await service.record_event(
            session, attempt=current, actor_id="student-1", operation_id="event-1",
            event_type="submission", expected_state_version=0,
            payload={"stage_requirements_met": True, "answer": "forged"},
        )
        assert "stage_requirements_met" not in event.payload
        aggregate = await service.evaluator.evaluate(session, attempt_id=attempt.id)
        assert aggregate.satisfied is False
        definitions = list((await session.scalars(select(RequirementDefinition).where(
            RequirementDefinition.task_stage_id == attempt.current_stage_id
        ))).all())
        for definition in definitions:
            await service.upsert_requirement_result(
                session, attempt=current, requirement_id=definition.id, status="SATISFIED",
                evaluator=definition.evaluator, evidence_refs=[f"evidence:{definition.id}"], version=1,
            )
        aggregate = await service.evaluator.evaluate(session, attempt_id=attempt.id)
        assert aggregate.satisfied is True
        assert aggregate.satisfied_count == aggregate.required_count
    await engine.dispose()


@pytest.mark.asyncio
async def test_operation_ledger_prevents_duplicate_and_stale_cas_fails():
    engine, factory = await make_database()
    service, _, attempt, _ = await seed(factory)
    async with factory() as session:
        current = await session.get(type(attempt), attempt.id)
        first, duplicate = await service.record_event(
            session, attempt=current, actor_id="student-1", operation_id="same-op",
            event_type="submission", expected_state_version=0, payload={},
        )
        replay, duplicate = await service.record_event(
            session, attempt=current, actor_id="student-1", operation_id="same-op",
            event_type="submission", expected_state_version=0, payload={},
        )
        assert duplicate is True and replay.id == first.id
        assert len((await session.scalars(select(OperationLedger).where(OperationLedger.operation_id == "same-op"))).all()) == 1
        with pytest.raises(BusinessRuleError, match="stale_state_version"):
            await service.record_event(
                session, attempt=await session.get(type(attempt), attempt.id), actor_id="student-1",
                operation_id="stale-op", event_type="submission", expected_state_version=0, payload={},
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_pending_intervention_survives_service_restart_and_unauthorized_teacher_is_rejected(tmp_path: Path):
    db_path = tmp_path / "restart.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine, factory = await make_database(url)
    service, _, attempt, _ = await seed(factory)
    async with factory() as session:
        current = await session.get(type(attempt), attempt.id)
        item, _ = await service.create_intervention(
            session, attempt=current, operation_id="intervention-op", reason="l2_authorization_required",
            evidence_refs=["evidence:1"],
        )
        item = await service.mark_checkpoint_result(
            session, intervention_id=item.id, succeeded=True
        )
        item_id = item.id
    await engine.dispose()

    restarted_engine = create_business_engine(url, sqlite_test_mode=True)
    restarted_factory = create_session_factory(restarted_engine)
    restarted_service = BusinessService()
    async with restarted_factory() as session:
        persisted = await session.get(Intervention, item_id)
        assert persisted is not None and persisted.status == "WAITING_TEACHER"
        with pytest.raises(BusinessRuleError, match="course_membership_required"):
            await restarted_service.begin_resume(
                session, intervention_id=item_id, teacher_id="teacher-2",
                resume_operation_id="unauthorized-resume", expected_state_version=0,
                response="unauthorized", allow_l2=True,
            )
    await restarted_engine.dispose()


@pytest.mark.asyncio
async def test_business_state_wins_over_checkpoint_version_and_attempts_are_isolated():
    engine, factory = await make_database()
    service, _, attempt1, attempt2 = await seed(factory)
    async with factory() as session:
        first = await session.get(type(attempt1), attempt1.id)
        item, _ = await service.create_intervention(
            session, attempt=first, operation_id="cp-conflict", reason="threshold", evidence_refs=[]
        )
        await service.mark_checkpoint_result(session, intervention_id=item.id, succeeded=True)
        await service.record_event(
            session, attempt=first, actor_id="student-1", operation_id="business-advanced",
            event_type="submission", expected_state_version=0, payload={},
        )
        # A checkpoint still claiming version 0 cannot override the authoritative attempt version 1.
        with pytest.raises(BusinessRuleError, match="stale_state_version"):
            await service.begin_resume(
                session, intervention_id=item.id, teacher_id="teacher-1",
                resume_operation_id="stale-resume", expected_state_version=0,
                response="stale checkpoint", allow_l2=False,
            )
        assert not (await session.scalars(select(TeachingEvent).where(TeachingEvent.attempt_id == attempt2.id))).all()
        with pytest.raises(BusinessRuleError, match="object_access_denied"):
            await service.authorize_attempt(
                session, attempt=await session.get(type(attempt2), attempt2.id), user_id="student-1", teacher=True
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_business_schema_creation_is_idempotent_and_downgrade_scope_is_business_only():
    engine, _ = await make_database()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, checkfirst=True)
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        assert {"users", "attempts", "interventions", "operation_ledger"}.issubset(tables)
        await connection.run_sync(Base.metadata.drop_all, checkfirst=True)
        assert not await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    await engine.dispose()


def test_bootstrap_sql_enforces_role_and_schema_separation():
    sql = (Path(__file__).parents[3] / "deploy" / "bootstrap-postgres.sql").read_text(encoding="utf-8")
    assert "ALTER ROLE teaching_app SET search_path = teaching_business, public" in sql
    assert "ALTER ROLE langgraph_cp SET search_path = langgraph_checkpoint, public" in sql
    assert "REVOKE ALL ON SCHEMA langgraph_checkpoint FROM teaching_app" in sql
    assert "REVOKE ALL ON SCHEMA teaching_business FROM langgraph_cp" in sql
    assert "REVOKE ALL ON SCHEMA teaching_business FROM PUBLIC" in sql
    assert "REVOKE ALL ON SCHEMA langgraph_checkpoint FROM PUBLIC" in sql


@pytest.mark.asyncio
async def test_intervention_checkpoint_failure_is_hidden_and_retry_is_idempotent():
    engine, factory = await make_database()
    service, _, attempt, _ = await seed(factory)
    async with factory() as session:
        current = await session.get(type(attempt), attempt.id)
        item, duplicate = await service.create_intervention(
            session, attempt=current, operation_id="checkpoint-op", reason="threshold",
            evidence_refs=["evidence:checkpoint"],
        )
        assert duplicate is False and item.status == "CREATING"
        failed = await service.mark_checkpoint_result(
            session, intervention_id=item.id, succeeded=False, error_code="simulated"
        )
        assert failed.status == "CHECKPOINT_FAILED"
        assert await service.list_waiting_interventions(session) == []

        same, duplicate = await service.create_intervention(
            session, attempt=current, operation_id="checkpoint-op", reason="threshold",
            evidence_refs=["evidence:checkpoint"],
        )
        assert duplicate is True and same.id == item.id
        await service.prepare_checkpoint_retry(session, intervention_id=item.id)
        waiting = await service.mark_checkpoint_result(
            session, intervention_id=item.id, succeeded=True
        )
        assert waiting.status == "WAITING_TEACHER"
        assert [record.id for record in await service.list_waiting_interventions(session)] == [item.id]
        assert len((await session.scalars(select(Intervention).where(
            Intervention.operation_id == "checkpoint-op"
        ))).all()) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_resume_failure_preserves_teacher_action_and_same_operation_retries():
    engine, factory = await make_database()
    service, _, attempt, _ = await seed(factory)
    async with factory() as session:
        current = await session.get(type(attempt), attempt.id)
        item, _ = await service.create_intervention(
            session, attempt=current, operation_id="resume-create", reason="threshold",
            evidence_refs=[],
        )
        await service.mark_checkpoint_result(session, intervention_id=item.id, succeeded=True)
        resuming, duplicate = await service.begin_resume(
            session, intervention_id=item.id, teacher_id="teacher-1",
            resume_operation_id="resume-op", expected_state_version=0,
            response="保留这条教师处理意见", allow_l2=True,
        )
        assert duplicate is False and resuming.status == "RESUMING"
        failed = await service.finish_resume(
            session, intervention_id=item.id, resume_operation_id="resume-op",
            succeeded=False, error_code="simulated",
        )
        assert failed.status == "RESUME_FAILED"
        assert failed.response == "保留这条教师处理意见"
        assert failed.assigned_teacher_id == "teacher-1"
        assert failed.allow_l2 is True

        retrying, duplicate = await service.begin_resume(
            session, intervention_id=item.id, teacher_id="teacher-1",
            resume_operation_id="resume-op", expected_state_version=0,
            response="不会覆盖原教师处理意见", allow_l2=False,
        )
        assert duplicate is True and retrying.status == "RESUMING"
        assert retrying.response == "保留这条教师处理意见"
        resolved = await service.finish_resume(
            session, intervention_id=item.id, resume_operation_id="resume-op", succeeded=True
        )
        assert resolved.status == "RESOLVED"
        replay, duplicate = await service.begin_resume(
            session, intervention_id=item.id, teacher_id="teacher-1",
            resume_operation_id="resume-op", expected_state_version=0,
            response="重复请求", allow_l2=False,
        )
        assert duplicate is True and replay.status == "RESOLVED"
        assert len((await session.scalars(select(OperationLedger).where(
            OperationLedger.operation_id == "resume-op"
        ))).all()) == 1
    await engine.dispose()
