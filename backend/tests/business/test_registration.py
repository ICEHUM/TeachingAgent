from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.agent.workspace import AttemptWorkspaceManager
from app.business.models import (
    Base,
    ClassEnrollment,
    CourseMembership,
    Task,
    TaskStage,
    TaskVersion,
)
from app.main import create_app


@pytest.mark.asyncio
async def test_teacher_class_student_registration_and_login(tmp_path):
    app = create_app(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'accounts.db').as_posix()}",
                     sqlite_test_mode=True, environment="test", dev_auth_enabled=True)
    app.state.workspace_manager = AttemptWorkspaceManager(tmp_path / "workspaces", tmp_path / "evidence")
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        teacher = await client.post("/api/product/auth/register/teacher", json={
            "account": "teacher_new", "password": "teacher-secret", "display_name": "张老师"})
        assert teacher.status_code == 201, teacher.text
        teacher_id = teacher.json()["user_id"]
        headers = {"X-User-Id": teacher_id}
        created = await client.post("/api/product/teacher/classes", headers=headers, json={"name": "Python 基础 1 班"})
        assert created.status_code == 201, created.text
        class_id = created.json()["id"]
        public = await client.get("/api/product/auth/classes")
        assert any(item["id"] == class_id for item in public.json()["classes"])

        student = await client.post("/api/product/auth/register/student", json={
            "account": "student_new", "password": "student-secret", "display_name": "李同学", "class_id": class_id})
        assert student.status_code == 201, student.text
        assert student.json()["attempt_id"] is None
        student_id = student.json()["user_id"]
        classroom = await client.get("/api/product/teacher/classes", headers=headers)
        assert classroom.json()["classes"][0]["students"][0]["account"] == "student_new"
        mine = await client.get("/api/product/student/classes", headers={"X-User-Id": student_id})
        assert mine.json()["classes"][0]["name"] == "Python 基础 1 班"

        course = await client.post("/api/product/teacher/courses", headers=headers, json={"name": "Python 入门"})
        assert course.status_code == 201, course.text
        bind = await client.put(f"/api/product/teacher/classes/{class_id}/course", headers=headers,
                                json={"course_id": course.json()["id"]})
        assert bind.status_code == 200, bind.text
        async with app.state.session_factory() as session:
            membership = await session.scalar(select(CourseMembership).where(
                CourseMembership.user_id == student_id, CourseMembership.course_id == course.json()["id"]))
            enrollment = await session.scalar(select(ClassEnrollment).where(ClassEnrollment.student_id == student_id))
            assert membership is not None and membership.role == "student"
            assert enrollment is not None

        login = await client.post("/api/product/auth/login", json={"account": "student_new", "password": "student-secret"})
        assert login.status_code == 200 and login.json()["user_id"] == student_id
        wrong = await client.post("/api/product/auth/login", json={"account": "student_new", "password": "wrong"})
        assert wrong.status_code == 401
        duplicate = await client.post("/api/product/auth/register/teacher", json={
            "account": "student_new", "password": "teacher-secret", "display_name": "另一位教师"})
        assert duplicate.status_code == 409
        invalid_class = await client.post("/api/product/auth/register/student", json={
            "account": "student_two", "password": "student-secret", "display_name": "王同学", "class_id": "missing"})
        assert invalid_class.status_code == 404
        student_create = await client.post("/api/product/teacher/classes", headers={"X-User-Id": student_id},
                                           json={"name": "无权限班级"})
        assert student_create.status_code == 403
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_student_receives_published_python_task_on_join(tmp_path):
    app = create_app(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'published.db').as_posix()}",
                     sqlite_test_mode=True, environment="test", dev_auth_enabled=True)
    app.state.workspace_manager = AttemptWorkspaceManager(tmp_path / "workspaces", tmp_path / "evidence")
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        teacher = await client.post("/api/product/auth/register/teacher", json={
            "account": "teacher_tasks", "password": "teacher-secret", "display_name": "任务教师"})
        headers = {"X-User-Id": teacher.json()["user_id"]}
        course = await client.post("/api/product/teacher/courses", headers=headers, json={"name": "Python 基础"})
        async with app.state.session_factory() as session:
            task = Task(course_id=course.json()["id"], task_key="PYB-01", title="两数相加")
            session.add(task)
            await session.flush()
            version = TaskVersion(task_id=task.id, version="v1", status="published", policy={})
            session.add(version)
            await session.flush()
            session.add(TaskStage(task_version_id=version.id, stage_key="write_program",
                                  title="编写与验证", position=0, aggregation="ALL_REQUIRED"))
            await session.commit()
        group = await client.post("/api/product/teacher/classes", headers=headers,
                                  json={"name": "循环练习班", "course_id": course.json()["id"]})
        student = await client.post("/api/product/auth/register/student", json={
            "account": "student_tasks", "password": "student-secret",
            "display_name": "任务学生", "class_id": group.json()["id"]})
        assert student.status_code == 201, student.text
        attempt_id = student.json()["attempt_id"]
        assert attempt_id
        source = app.state.workspace_manager.source_directory(attempt_id) / "main.py"
        assert source.exists() and "third" in source.read_text(encoding="utf-8")
        assignments = await client.get("/api/product/student/assignments",
                                       headers={"X-User-Id": student.json()["user_id"]})
        assert assignments.status_code == 200
        assert assignments.json()["assignments"][0]["attempt_id"] == attempt_id
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_registration_disabled_outside_demo(tmp_path):
    app = create_app(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'disabled.db').as_posix()}",
                     sqlite_test_mode=True, environment="development", dev_auth_enabled=True)
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/product/auth/register/teacher", json={
            "account": "teacher_new", "password": "teacher-secret", "display_name": "张老师"})
        assert response.status_code == 404
    await app.state.business_engine.dispose()
