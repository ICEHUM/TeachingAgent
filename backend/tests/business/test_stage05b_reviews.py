from __future__ import annotations

import hashlib

import httpx
import pytest
from sqlalchemy import select

from app.agent.workspace import AttemptWorkspaceManager
from app.business.models import (
    Base,
    Course,
    CourseMembership,
    FormalGrade,
    RequirementDefinition,
    RequirementResult,
    TaskStage,
    User,
)
from app.business.service import BusinessService
from app.main import create_app


async def setup_app(tmp_path):
    database = tmp_path / "stage05b.db"
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        sqlite_test_mode=True,
        environment="test",
        dev_auth_enabled=True,
    )
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    manager = AttemptWorkspaceManager(root=tmp_path / "attempts", evidence_root=tmp_path / "evidence")
    app.state.workspace_manager = manager
    service = BusinessService()
    async with app.state.session_factory() as session:
        teacher = User(id="t1", email="t1@test", display_name="陈老师", system_role="teacher")
        student = User(id="s1", email="s1@test", display_name="林雨", system_role="student")
        other = User(id="s2", email="s2@test", display_name="周宁", system_role="student")
        course = Course(id="c1", code="AI-05B", name="AI 应用开发实训")
        session.add_all([teacher, student, other, course])
        session.add_all([
            CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
            CourseMembership(course_id=course.id, user_id=student.id, role="student"),
            CourseMembership(course_id=course.id, user_id=other.id, role="student"),
        ])
        await session.commit()
        version = await service.create_faq_version(session, course_id=course.id, actor_id=teacher.id)
        attempt = await service.create_attempt(session, task_version_id=version.id, learner_id=student.id, mode="guided_practice")
        other_attempt = await service.create_attempt(session, task_version_id=version.id, learner_id=other.id, mode="guided_practice")
        stage = await session.scalar(select(TaskStage).where(TaskStage.task_version_id == version.id, TaskStage.stage_key == "implement_retrieval"))
        attempt.current_stage_id = stage.id
        await session.commit()
    source = manager.source_directory(attempt.id)
    source.mkdir(parents=True, exist_ok=True)
    (source / "faq_app.py").write_text("def retrieve(question, sources):\n    return []\n", encoding="utf-8")
    return app, manager, service, attempt, other_attempt, teacher, student, other


@pytest.mark.asyncio
async def test_submission_is_idempotent_and_isolated(tmp_path):
    app, _manager, _service, attempt, _other_attempt, teacher, student, other = await setup_app(tmp_path)
    headers = {"X-User-Id": student.id}
    other_headers = {"X-User-Id": other.id}
    teacher_headers = {"X-User-Id": teacher.id}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        saved = await client.put(
            f"/api/product/attempts/{attempt.id}/files/faq_app.py",
            headers=headers,
            json={"content": "def retrieve(question, sources):\n    return sources\n", "expected_hash": hashlib.sha256(b"def retrieve(question, sources):\n    return []\n").hexdigest()},
        )
        assert saved.status_code == 200
        snapshot = await client.post(
            f"/api/product/attempts/{attempt.id}/snapshots",
            headers=headers,
            json={"operation_id": "snapshot-01", "expected_file_hash": saved.json()["hash"]},
        )
        assert snapshot.status_code == 200
        first = await client.post(
            f"/api/product/attempts/{attempt.id}/submissions",
            headers=headers,
            json={"operation_id": "submit-01", "snapshot_id": snapshot.json()["id"], "explanation": "资料能加载，但已知问题检索仍为空。"},
        )
        assert first.status_code == 200
        duplicate = await client.post(
            f"/api/product/attempts/{attempt.id}/submissions",
            headers=headers,
            json={"operation_id": "submit-01", "snapshot_id": snapshot.json()["id"], "explanation": "另一段说明"},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["id"] == first.json()["id"]
        same_snapshot = await client.post(
            f"/api/product/attempts/{attempt.id}/submissions",
            headers=headers,
            json={"operation_id": "submit-02", "snapshot_id": snapshot.json()["id"], "explanation": "重复快照"},
        )
        assert same_snapshot.json()["id"] == first.json()["id"]
        forbidden = await client.get(f"/api/product/submissions/{first.json()['id']}", headers=other_headers)
        assert forbidden.status_code == 403
        classroom = await client.get("/api/product/teacher/classroom", headers=teacher_headers)
        assert classroom.status_code == 200
        attention_ids = [item["attempt_id"] for item in classroom.json()["groups"]["attention"]]
        assert attempt.id in attention_ids
        assert any(item["reason"] == "pending_review" for item in classroom.json()["groups"]["attention"] if item["attempt_id"] == attempt.id)
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_teacher_publish_requires_confirmation_and_ignores_client_grade(tmp_path):
    app, _manager, _service, attempt, _other_attempt, teacher, student, _other = await setup_app(tmp_path)
    headers = {"X-User-Id": student.id}
    teacher_headers = {"X-User-Id": teacher.id}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        original = "def retrieve(question, sources):\n    return []\n"
        saved = await client.put(
            f"/api/product/attempts/{attempt.id}/files/faq_app.py",
            headers=headers,
            json={"content": original + "\n# edited\n", "expected_hash": hashlib.sha256(original.encode()).hexdigest()},
        )
        snapshot = await client.post(
            f"/api/product/attempts/{attempt.id}/snapshots",
            headers=headers,
            json={"operation_id": "snapshot-grade", "expected_file_hash": saved.json()["hash"]},
        )
        submitted = await client.post(
            f"/api/product/attempts/{attempt.id}/submissions",
            headers=headers,
            json={"operation_id": "submit-grade", "snapshot_id": snapshot.json()["id"], "explanation": "已记录检索为空的观察。"},
        )
        view = await client.get(f"/api/product/submissions/{submitted.json()['id']}", headers=teacher_headers)
        assert view.status_code == 200
        assert view.json()["formal_grade"] is None
        review_id = view.json()["review"]["id"]
        assert view.json()["review"]["can_publish"] is False
        empty_publish = await client.post(
            f"/api/product/teacher/reviews/{review_id}/publish",
            headers=teacher_headers,
            json={"operation_id": "pub-early"},
        )
        assert empty_publish.status_code == 409
        assert empty_publish.json()["detail"]["code"] == "unconfirmed_rubric_item"
        draft = await client.put(
            f"/api/product/teacher/reviews/{review_id}",
            headers=teacher_headers,
            json={"items": [
                {"key": item["key"], "score": item["max_score"], "reason": "依据当前提交 Snapshot 的证据确认。", "confirmed": True}
                for item in view.json()["rubric"]
            ]},
        )
        assert draft.status_code == 200
        assert all(item["teacher"]["status"] == "confirmed" for item in draft.json()["rubric"])
        student_publish = await client.post(
            f"/api/product/teacher/reviews/{review_id}/publish",
            headers=headers,
            json={"operation_id": "publish-student"},
        )
        assert student_publish.status_code == 403
        published = await client.post(
            f"/api/product/teacher/reviews/{review_id}/publish",
            headers=teacher_headers,
            json={"operation_id": "publish-01"},
        )
        assert published.status_code == 200
        assert published.json()["formal_grade"]["total_score"] == 100
        replay = await client.post(
            f"/api/product/teacher/reviews/{review_id}/publish",
            headers=teacher_headers,
            json={"operation_id": "publish-01"},
        )
        assert replay.status_code == 200
        recap = await client.get(f"/api/product/submissions/{submitted.json()['id']}", headers=headers)
        assert recap.json()["formal_grade"]["total_score"] == 100
        assert recap.json()["rubric"][0]["ai"]["score"] is None
        teacher_review = await client.post(
            f"/api/product/teacher/submissions/{submitted.json()['id']}/requirement-reviews",
            headers=teacher_headers,
            json={"operation_id": "rev-delivery", "requirement_key": "delivery_review", "status": "SATISFIED", "reason": "README 与提交说明可以验收。"},
        )
        assert teacher_review.status_code == 200
        assert teacher_review.json()["evaluator"] == "teacher_review_v1"
        async with app.state.session_factory() as session:
            grades = list((await session.scalars(select(FormalGrade))).all())
            assert len(grades) == 1
            result = await session.scalar(select(RequirementResult).where(RequirementResult.operation_id == "rev-delivery"))
            assert result.status == "SATISFIED"
            definition = await session.get(RequirementDefinition, result.requirement_id)
            assert definition.requirement_key == "delivery_review"
            assert result.snapshot_id == snapshot.json()["id"]
    await app.state.business_engine.dispose()
