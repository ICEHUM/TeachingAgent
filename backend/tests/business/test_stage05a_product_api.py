from __future__ import annotations

import hashlib

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.agent.workspace import AttemptWorkspaceManager
from app.business.models import Base, Course, CourseMembership, TaskStage, User
from app.business.service import BusinessService
from app.business.stage05_api import _relative_file
from app.main import create_app


@pytest.mark.asyncio
async def test_student_workspace_save_snapshot_and_real_workbench(tmp_path):
    database = tmp_path / "stage05.db"
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        sqlite_test_mode=True,
        environment="test",
        dev_auth_enabled=True,
    )
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    manager = AttemptWorkspaceManager(
        root=tmp_path / "attempts", evidence_root=tmp_path / "evidence"
    )
    app.state.workspace_manager = manager
    service = BusinessService()
    async with app.state.session_factory() as session:
        teacher = User(
            id="stage05-teacher",
            email="teacher@stage05.test",
            display_name="陈老师",
            system_role="teacher",
        )
        learner = User(
            id="stage05-student",
            email="student@stage05.test",
            display_name="林雨",
            system_role="student",
        )
        course = Course(id="stage05-course", code="STAGE05", name="AI 应用开发实训")
        session.add_all([teacher, learner, course])
        session.add_all(
            [
                CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
                CourseMembership(course_id=course.id, user_id=learner.id, role="student"),
            ]
        )
        await session.commit()
        version = await service.create_faq_version(
            session, course_id=course.id, actor_id=teacher.id
        )
        attempt = await service.create_attempt(
            session,
            task_version_id=version.id,
            learner_id=learner.id,
            mode="guided_practice",
        )
        stage = await session.scalar(
            select(TaskStage).where(
                TaskStage.task_version_id == version.id,
                TaskStage.stage_key == "implement_retrieval",
            )
        )
        attempt.current_stage_id = stage.id
        await session.commit()
    source = manager.source_directory(attempt.id)
    original = "def retrieve(question, sources):\n    return []\n"
    (source / "faq_app.py").write_text(original, encoding="utf-8")
    headers = {"X-User-Id": learner.id}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        workbench = await client.get(
            f"/api/product/attempts/{attempt.id}/workbench", headers=headers
        )
        assert workbench.status_code == 200
        assert workbench.json()["stage"]["key"] == "implement_retrieval"
        assert workbench.json()["files"][0]["path"] == "faq_app.py"

        fixed = "def retrieve(question, sources):\n    return [item for item in sources if item['question'] == question]\n"
        saved = await client.put(
            f"/api/product/attempts/{attempt.id}/files/faq_app.py",
            headers=headers,
            json={
                "content": fixed,
                "expected_hash": hashlib.sha256(original.encode()).hexdigest(),
            },
        )
        assert saved.status_code == 200
        snapshot = await client.post(
            f"/api/product/attempts/{attempt.id}/snapshots",
            headers=headers,
            json={
                "operation_id": "stage05-test-snapshot",
                "expected_file_hash": saved.json()["hash"],
            },
        )
        assert snapshot.status_code == 200
        assert snapshot.json()["label"] == "Snapshot A"

        conflict = await client.put(
            f"/api/product/attempts/{attempt.id}/files/faq_app.py",
            headers=headers,
            json={"content": fixed + "\n", "expected_hash": "stale"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "snapshot_conflict"
    await app.state.business_engine.dispose()


def test_workspace_path_traversal_is_rejected(tmp_path):
    with pytest.raises(HTTPException) as error:
        _relative_file(tmp_path, "../secret.py")
    assert error.value.detail["code"] == "path_traversal"
