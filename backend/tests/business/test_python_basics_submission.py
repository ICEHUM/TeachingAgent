"""PYB-01 submission only accepts snapshot-bound public and hidden evidence."""

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
    RubricDefinition,
    Submission,
    Task,
    TaskStage,
    TaskVersion,
    User,
)
from app.business.python_basics import OBJECTIVE, REQUIREMENTS, RUBRIC, STAGE_KEY, TASK_KEY
from app.business.service import BusinessService
from app.main import create_app


@pytest.mark.asyncio
async def test_public_check_then_hidden_evidence_required_for_submission(tmp_path):
    app = create_app(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'task.db').as_posix()}",
                     sqlite_test_mode=True, environment="test", dev_auth_enabled=True)
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app.state.workspace_manager = AttemptWorkspaceManager(
        root=tmp_path / "attempts", evidence_root=tmp_path / "evidence")
    service = BusinessService()
    ids = {"teacher": "pyb-teacher", "student": "pyb-student", "course": "pyb-course",
           "task": "pyb-task", "version": "pyb-version", "stage": "pyb-stage", "attempt": "pyb-attempt"}
    async with app.state.session_factory() as session:
        session.add_all([
            User(id=ids["teacher"], email="teacher@pyb.test", display_name="教师", system_role="teacher"),
            User(id=ids["student"], email="student@pyb.test", display_name="学生", system_role="student"),
            Course(id=ids["course"], code="PYB-TEST", name="Python 基础编程"),
        ])
        session.add_all([
            CourseMembership(course_id=ids["course"], user_id=ids["teacher"], role="teacher"),
            CourseMembership(course_id=ids["course"], user_id=ids["student"], role="student"),
            Task(id=ids["task"], course_id=ids["course"], task_key=TASK_KEY, title="两数相加"),
            TaskVersion(id=ids["version"], task_id=ids["task"], version="v1", status="published",
                        policy={"stage_objectives": {STAGE_KEY: OBJECTIVE}}),
            TaskStage(id=ids["stage"], task_version_id=ids["version"], stage_key=STAGE_KEY,
                      title="编写与验证", position=0),
            Attempt(id=ids["attempt"], task_version_id=ids["version"], learner_id=ids["student"],
                    current_stage_id=ids["stage"], mode="guided_practice", status="active"),
        ])
        for index, (key, name, kind, evaluator, tool) in enumerate(REQUIREMENTS):
            session.add(RequirementDefinition(id=f"pyb-req-{index}", task_stage_id=ids["stage"],
                                              requirement_key=key, kind=kind, required=True,
                                              version=1, evaluator=evaluator,
                                              config={"display_name": name, "tool": tool}))
        for index, (key, title, score, keys) in enumerate(RUBRIC):
            session.add(RubricDefinition(id=f"pyb-rubric-{index}", task_version_id=ids["version"],
                                         item_key=key, title=title, max_score=score,
                                         position=index, requirement_keys=list(keys)))
        await session.commit()
    source = app.state.workspace_manager.source_directory(ids["attempt"])
    code = "first = int(input())\nsecond = int(input())\nprint(first + second)\n"
    (source / "main.py").write_text(code, encoding="utf-8")
    headers = {"X-User-Id": ids["student"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        workbench = await client.get(f"/api/product/attempts/{ids['attempt']}/workbench", headers=headers)
        assert workbench.status_code == 200
        assert workbench.json()["task"]["key"] == TASK_KEY
        snapshot = await client.post(f"/api/product/attempts/{ids['attempt']}/snapshots", headers=headers,
                                     json={"operation_id": "pyb-test-snapshot-1",
                                           "expected_file_hash": hashlib.sha256(code.encode()).hexdigest(),
                                           "expected_file_path": "main.py"})
        assert snapshot.status_code == 200
        snapshot_id = snapshot.json()["id"]
        submission_body = {"operation_id": "pyb-test-submission-1", "snapshot_id": snapshot_id,
                           "explanation": "已核对公开样例。"}
        blocked = await client.post(f"/api/product/attempts/{ids['attempt']}/submissions",
                                    headers=headers, json=submission_body)
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "public_check_required"

        async with app.state.session_factory() as session:
            attempt = await session.get(Attempt, ids["attempt"])
            await service.upsert_requirement_result(session, attempt=attempt,
                requirement_id="pyb-req-0", snapshot_id=snapshot_id,
                operation_id="pyb-test-public-1", status="SATISFIED",
                evaluator="test-public", evidence_refs=["artifact://public"], version=1)
        classroom = await client.get("/api/product/teacher/classroom", headers={"X-User-Id": ids["teacher"]})
        assert classroom.status_code == 200
        assert classroom.json()["courses"] == [{"id": ids["course"], "code": "PYB-TEST", "name": "Python 基础编程"}]
        classroom_item = classroom.json()["groups"]["progress"][0]
        assert classroom_item["course_id"] == ids["course"]
        assert classroom_item["task_key"] == TASK_KEY
        assert classroom_item["public_check_status"] == "SATISFIED"

        class FailedHidden:
            async def run_event(self, **_kwargs):
                return {"last_tool_status": "student_failure"}

        app.state.teaching_runtime = FailedHidden()
        failed = await client.post(f"/api/product/attempts/{ids['attempt']}/submissions",
                                   headers=headers, json=submission_body)
        assert failed.status_code == 409
        assert failed.json()["detail"]["code"] == "hidden_check_failed"
        assert "-4" not in failed.text

        class PassedHidden:
            async def run_event(self, **_kwargs):
                async with app.state.session_factory() as session:
                    attempt = await session.get(Attempt, ids["attempt"])
                    await service.upsert_requirement_result(session, attempt=attempt,
                        requirement_id="pyb-req-1", snapshot_id=snapshot_id,
                        operation_id="pyb-test-hidden-1", status="SATISFIED",
                        evaluator="test-hidden", evidence_refs=["artifact://hidden"], version=1)
                return {"last_tool_status": "succeeded"}

        app.state.teaching_runtime = PassedHidden()
        submitted = await client.post(f"/api/product/attempts/{ids['attempt']}/submissions",
                                      headers=headers, json=submission_body)
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["snapshot_id"] == snapshot_id
        teacher = await client.get(f"/api/product/teacher/attempts/{ids['attempt']}",
                                   headers={"X-User-Id": ids["teacher"]})
        assert teacher.status_code == 200
        assert teacher.json()["latest_submission"]["snapshot_id"] == snapshot_id
        assert {item["key"]: item["status"] for item in teacher.json()["requirements"]} == {
            "addition_public_tests": "SATISFIED",
            "addition_hidden_tests": "SATISFIED",
        }
        async with app.state.session_factory() as session:
            records = list((await session.scalars(select(Submission).where(
                Submission.attempt_id == ids["attempt"])) ).all())
            assert len(records) == 1
    await app.state.business_engine.dispose()
