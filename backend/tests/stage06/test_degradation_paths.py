from __future__ import annotations

import hashlib

import httpx
import pytest
from sqlalchemy import select

from app.agent.workspace import AttemptWorkspaceManager
from app.business.models import (
    Attempt,
    Base,
    Course,
    CourseMembership,
    RequirementDefinition,
    Snapshot,
    TaskStage,
    User,
)
from app.business.service import BusinessService
from app.main import create_app


async def seeded_app(tmp_path):
    database = tmp_path / "stage06-degradation.db"
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        sqlite_test_mode=True,
        environment="test",
        dev_auth_enabled=True,
    )
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app.state.workspace_manager = AttemptWorkspaceManager(
        root=tmp_path / "attempts", evidence_root=tmp_path / "evidence"
    )
    service = BusinessService()
    async with app.state.session_factory() as session:
        teacher = User(id="teacher", email="teacher@stage06.test", display_name="教师", system_role="teacher")
        student = User(id="student", email="student@stage06.test", display_name="学生", system_role="student")
        course = Course(id="course", code="STAGE06", name="Stage 06")
        session.add_all([teacher, student, course])
        session.add_all([
            CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
            CourseMembership(course_id=course.id, user_id=student.id, role="student"),
        ])
        await session.commit()
        version = await service.create_faq_version(session, course_id=course.id, actor_id=teacher.id)
        attempt = await service.create_attempt(session, task_version_id=version.id, learner_id=student.id, mode="guided_practice")
        stage = await session.scalar(select(TaskStage).where(TaskStage.task_version_id == version.id, TaskStage.stage_key == "implement_retrieval"))
        attempt.current_stage_id = stage.id
        await session.commit()
    return app, attempt.id, student.id


@pytest.mark.asyncio
async def test_openhands_outage_preserves_editing_and_does_not_count_student_failure(tmp_path):
    app, attempt_id, student_id = await seeded_app(tmp_path)
    app.state.teaching_runtime = None
    source = app.state.workspace_manager.source_directory(attempt_id)
    original = "def retrieve(question, sources):\n    return []\n"
    (source / "faq_app.py").write_text(original, encoding="utf-8")
    headers = {"X-User-Id": student_id}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put(
            f"/api/product/attempts/{attempt_id}/files/faq_app.py",
            headers=headers,
            json={"content": original + "# saved\n", "expected_hash": hashlib.sha256(original.encode()).hexdigest()},
        )
        assert saved.status_code == 200
        snapshot = await client.post(
            f"/api/product/attempts/{attempt_id}/snapshots",
            headers=headers,
            json={"operation_id": "stage06-outage-snapshot", "expected_file_hash": saved.json()["hash"]},
        )
        assert snapshot.status_code == 200
        async with app.state.session_factory() as session:
            attempt = await session.get(Attempt, attempt_id)
            requirement = await session.scalar(
                select(RequirementDefinition).where(
                    RequirementDefinition.task_stage_id == attempt.current_stage_id,
                    RequirementDefinition.requirement_key == "retrieval_public_tests",
                )
            )
            stored_snapshot = await session.get(Snapshot, snapshot.json()["id"])
            await BusinessService().upsert_requirement_result(
                session,
                attempt=attempt,
                requirement_id=requirement.id,
                snapshot_id=stored_snapshot.id,
                operation_id="stage06-existing-evidence",
                status="NOT_SATISFIED",
                evaluator=requirement.evaluator,
                evidence_refs=["evidence://stage06/existing"],
                version=requirement.version,
            )
        evidence = await client.get(
            f"/api/product/attempts/{attempt_id}/evidence/stage06-existing-evidence",
            headers=headers,
        )
        assert evidence.status_code == 200
        assert evidence.json()["status"] == "NOT_SATISFIED"
        run = await client.post(
            f"/api/product/attempts/{attempt_id}/runs",
            headers=headers,
            json={"operation_id": "stage06-outage-run", "snapshot_id": snapshot.json()["id"], "expected_state_version": 0, "observation": ""},
        )
        assert run.status_code == 503
        assert run.json()["detail"]["code"] == "openhands_unavailable"
        state = await client.get(f"/api/attempts/{attempt_id}", headers=headers)
        assert state.json()["state_version"] == 0
    async with app.state.session_factory() as session:
        attempt = await session.get(Attempt, attempt_id)
        assert attempt.student_failure_count == 0
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_sse_reconnect_backfills_event_after_cursor(tmp_path):
    app, attempt_id, student_id = await seeded_app(tmp_path)
    service = BusinessService()
    async with app.state.session_factory() as session:
        attempt = await session.get(Attempt, attempt_id)
        await service.record_event(session, attempt=attempt, actor_id=student_id, operation_id="stage06-sse-event", event_type="submission", expected_state_version=0, payload={"event_type": "submission"})
    from app.business.stage05_api import event_stream

    class Request:
        def __init__(self):
            self.app = app

        async def is_disconnected(self):
            return False

    async with app.state.session_factory() as session:
        student = await session.get(User, student_id)
        response = await event_stream(
            attempt_id, Request(), session, student, since_state_version=0
        )
        iterator = response.body_iterator
        ready = await anext(iterator)
        event = await anext(iterator)
        await iterator.aclose()
    chunks = str(ready) + str(event)
    assert "id: 1" in chunks
    assert "event: teaching_event" in chunks
    await app.state.business_engine.dispose()

@pytest.mark.asyncio
async def test_postgresql_outage_blocks_submission_instead_of_false_success():
    app = create_app(
        database_url="postgresql+psycopg://nobody:invalid@127.0.0.1:1/unavailable?connect_timeout=1",
        environment="test",
        dev_auth_enabled=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/product/attempts/missing/submissions",
            headers={"X-User-Id": "missing"},
            json={"operation_id": "stage06-db-outage", "snapshot_id": "missing", "explanation": ""},
        )
    assert response.status_code >= 500
    assert response.status_code not in {200, 201}
    await app.state.business_engine.dispose()
