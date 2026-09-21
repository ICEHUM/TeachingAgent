from __future__ import annotations

import httpx
import pytest

from app.business.models import (
    Attempt,
    Base,
    Course,
    CourseMembership,
    Task,
    TaskStage,
    TaskVersion,
    User,
)
from app.main import create_app


async def app_with_demo(tmp_path, monkeypatch, *, environment="test", dev_auth=True):
    monkeypatch.setenv("DEMO_STUDENT_PASSWORD", "student-secret")
    monkeypatch.setenv("DEMO_TEACHER_PASSWORD", "teacher-secret")
    app = create_app(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'login.db').as_posix()}", sqlite_test_mode=True, environment=environment, dev_auth_enabled=dev_auth)
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.state.session_factory() as session:
        teacher = User(id="teacher", email="teacher.rc06@demo.invalid", display_name="演示教师", system_role="teacher")
        student = User(id="student", email="student.rc06@demo.invalid", display_name="演示学生", system_role="student")
        course = Course(id="course", code="DEMO-FAQ-001-RC06", name="演示班")
        task = Task(id="task", course_id="course", task_key="FAQ-001", title="FAQ")
        version = TaskVersion(id="version", task_id="task", version="v1", status="published", policy={})
        stage = TaskStage(id="stage", task_version_id="version", stage_key="understand", title="理解需求", position=0, aggregation="ALL_REQUIRED")
        attempt = Attempt(id="attempt", task_version_id="version", learner_id="student", current_stage_id="stage", mode="guided_practice", status="active")
        session.add_all([teacher, student, course, task, version, stage, attempt])
        session.add_all([CourseMembership(course_id="course", user_id="teacher", role="teacher"), CourseMembership(course_id="course", user_id="student", role="student")])
        await session.commit()
    return app


@pytest.mark.asyncio
async def test_demo_login_resolves_real_student_and_teacher(tmp_path, monkeypatch):
    app = await app_with_demo(tmp_path, monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        student = await client.post("/api/product/auth/demo-login", json={"account": "demo_student", "password": "student-secret"})
        teacher = await client.post("/api/product/auth/demo-login", json={"account": "demo_teacher", "password": "teacher-secret"})
    assert student.status_code == 200
    assert student.json() == {"role": "student", "display_name": "演示学生", "user_id": "student", "attempt_id": "attempt"}
    assert teacher.status_code == 200
    assert teacher.json() == {"role": "teacher", "display_name": "演示教师", "user_id": "teacher", "attempt_id": None}
    assert "password" not in student.text.lower()


@pytest.mark.asyncio
async def test_demo_login_rejects_invalid_credentials(tmp_path, monkeypatch):
    app = await app_with_demo(tmp_path, monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/product/auth/demo-login", json={"account": "demo_student", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_demo_login_unavailable_when_dev_auth_disabled(tmp_path, monkeypatch):
    app = await app_with_demo(tmp_path, monkeypatch, dev_auth=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/product/auth/demo-login", json={"account": "demo_student", "password": "student-secret"})
    assert response.status_code == 404
