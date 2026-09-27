from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.agent.workspace import AttemptWorkspaceManager
from app.business.faq import DEFAULT_TASK_POLICY
from app.business.models import Base, Course, CourseMembership, TaskStage, User
from app.business.service import BusinessService
from app.business.stage05_api import (
    SELF_SERVICE_TOOL_LABELS,
    SELF_SERVICE_TOOLS,
    _console_view,
    _snapshot_requirement_evidence,
)
from app.main import create_app


class StubRuntime:
    """Records the console run contract and writes the artifact the endpoint reads."""

    def __init__(self, manager: AttemptWorkspaceManager):
        self.manager = manager
        self.calls: list[dict[str, Any]] = []

    async def run_event(self, *, attempt_id, event, automatic_followup=True):
        self.calls.append(
            {
                "attempt_id": attempt_id,
                "event": dict(event),
                "automatic_followup": automatic_followup,
            }
        )
        digest = hashlib.sha256(f"tool:{event['operation_id']}".encode()).hexdigest()
        path = self.manager.evidence_directory(attempt_id) / f"{digest}.artifact.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "request": {"tool_name": event["requested_tool"]},
                    "status": "student_failure",
                    "code": "student_program_failed",
                    "stdout": "资料条数：3\n",
                    "stderr": (
                        'Traceback (most recent call last):\n  File "/workspace/student/faq_app.py",'
                        ' line 7, in <module>\n    print(retrieve(None, []))\nNameError: name'
                        " 'retrieve' is not defined\n"
                    ),
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                    "details": {"exit_code": 1},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {"state_version": event["expected_state_version"] + 1, "last_tool_status": "student_failure"}


def test_trace_console_view_returns_program_output_and_last_student_error_line(tmp_path):
    manager = AttemptWorkspaceManager(root=tmp_path / "attempts", evidence_root=tmp_path / "evidence")
    operation_id = "debug-view-01"
    digest = hashlib.sha256(f"tool:{operation_id}".encode()).hexdigest()
    path = manager.evidence_directory("attempt-1") / f"{digest}.artifact.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "request": {"tool_name": "run_python_trace", "task_version": "PYB-01-v1"},
        "status": "student_failure", "code": "runtime_error",
        "stdout": "__TEACHING_EVIDENCE__={...}", "stderr": "",
        "details": {"structured": {
            "stdout": "before error\n",
            "stderr": 'File "/workspace/student/main.py", line 1\nFile "/workspace/student/main.py", line 3\nZeroDivisionError',
            "exit_code": 1,
            "trace": [{"file": "main.py", "line": 1, "locals": {}}, {"file": "main.py", "line": 3, "locals": {"first": "2"}}],
            "trace_truncated": False,
        }},
    }), encoding="utf-8")
    view = _console_view(
        attempt=SimpleNamespace(id="attempt-1"), operation_id=operation_id,
        requested_tool="run_python_trace", snapshot=SimpleNamespace(id="snapshot-1", sequence=1),
        state={}, manager=manager,
    )
    assert view["stdout"] == "before error\n"
    assert view["exit_code"] == 1
    assert view["location"] == {"file": "main.py", "line": 3}
    assert view["trace"][1]["locals"] == {"first": "2"}
    assert view["sample_input"] == "2\n3\n"


@pytest.mark.asyncio
async def test_manual_guidance_uses_only_current_public_diagnosis(tmp_path):
    manager = AttemptWorkspaceManager(root=tmp_path / "attempts", evidence_root=tmp_path / "evidence")
    attempt_id, snapshot_id = "guidance-attempt", "current-snapshot"
    public_operation = "tool:guidance-public-check"
    hidden_operation = "tool:guidance-hidden-check"
    directory = manager.evidence_directory(attempt_id)

    def artifact_path(operation_id: str):
        return directory / f"{hashlib.sha256(operation_id.encode()).hexdigest()}.artifact.json"

    artifact_path(public_operation).write_text(json.dumps({
        "request": {"attempt_id": attempt_id, "snapshot_id": snapshot_id,
                    "tool_name": "run_python_public_tests"},
        "details": {"structured": {
            "checks": [{"passed": False, "diagnosis_code": "undefined_name"}],
            "error": 'File "/workspace/student/main.py", line 5\nNameError: name \'sum_value\' is not defined',
        }},
    }), encoding="utf-8")
    artifact_path(hidden_operation).write_text(json.dumps({
        "request": {"attempt_id": attempt_id, "snapshot_id": snapshot_id,
                    "tool_name": "run_python_hidden_tests"},
        "details": {"structured": {
            "checks": [{"passed": False, "diagnosis_code": "secret_hidden_case"}],
        }},
    }), encoding="utf-8")
    public = SimpleNamespace(operation_id=public_operation, status="NOT_SATISFIED",
                             evidence_refs=["artifact://public"])
    hidden = SimpleNamespace(operation_id=hidden_operation, status="NOT_SATISFIED",
                             evidence_refs=["artifact://hidden"])
    rows = [(public, "addition_public_tests"), (hidden, "addition_hidden_tests")]
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)))

    refs, summary = await _snapshot_requirement_evidence(
        session, manager=manager, attempt_id=attempt_id, snapshot_id=snapshot_id,
    )
    assert refs == ["artifact://public", "artifact://hidden"]
    assert "addition_public_tests=NOT_SATISFIED" in summary
    assert "observed_public_diagnosis=undefined_name" in summary
    assert "observed_public_error=NameError" in summary
    assert "observed_public_location=main.py:5" in summary
    assert "secret_hidden_case" not in summary

    _, stale_summary = await _snapshot_requirement_evidence(
        session, manager=manager, attempt_id=attempt_id, snapshot_id="other-snapshot",
    )
    assert "observed_public_diagnosis" not in stale_summary


async def setup_app(tmp_path):
    database = tmp_path / "program-runs.db"
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
        teacher = User(id="pr-teacher", email="pr-teacher@test", display_name="陈老师", system_role="teacher")
        student = User(id="pr-student", email="pr-student@test", display_name="林雨", system_role="student")
        other = User(id="pr-other", email="pr-other@test", display_name="周宁", system_role="student")
        course = Course(id="pr-course", code="AI-PR", name="AI 应用开发实训")
        session.add_all([teacher, student, other, course])
        session.add_all(
            [
                CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
                CourseMembership(course_id=course.id, user_id=student.id, role="student"),
                CourseMembership(course_id=course.id, user_id=other.id, role="student"),
            ]
        )
        await session.commit()
        version = await service.create_faq_version(session, course_id=course.id, actor_id=teacher.id)
        attempt = await service.create_attempt(
            session, task_version_id=version.id, learner_id=student.id, mode="guided_practice"
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
    source.mkdir(parents=True, exist_ok=True)
    (source / "faq_app.py").write_text("def retrieve(question, sources):\n    return []\n", encoding="utf-8")
    runtime = StubRuntime(manager)
    app.state.teaching_runtime = runtime
    return app, manager, runtime, service, attempt, teacher, student, other


async def prepare_snapshot(client, attempt, headers) -> dict[str, Any]:
    content = "def retrieve(question, sources):\n    return []\n"
    saved = await client.put(
        f"/api/product/attempts/{attempt.id}/files/faq_app.py",
        headers=headers,
        json={"content": content, "expected_hash": hashlib.sha256(content.encode()).hexdigest()},
    )
    assert saved.status_code == 200
    snapshot = await client.post(
        f"/api/product/attempts/{attempt.id}/snapshots",
        headers=headers,
        json={"operation_id": "pr-snapshot-01", "expected_file_hash": saved.json()["hash"]},
    )
    assert snapshot.status_code == 200
    workbench = await client.get(f"/api/product/attempts/{attempt.id}/workbench", headers=headers)
    assert workbench.status_code == 200
    return workbench.json()


@pytest.mark.asyncio
async def test_program_run_returns_raw_output_without_followup(tmp_path):
    app, _manager, runtime, _service, attempt, _teacher, student, _other = await setup_app(tmp_path)
    headers = {"X-User-Id": student.id}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        workbench = await prepare_snapshot(client, attempt, headers)
        response = await client.post(
            f"/api/product/attempts/{attempt.id}/program-runs",
            headers=headers,
            json={
                "operation_id": "pr-run-01",
                "snapshot_id": workbench["latest_snapshot"]["id"],
                "expected_state_version": workbench["attempt"]["state_version"],
                "tool": "run_student_program",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["tool"] == "run_student_program"
        assert body["label"] == SELF_SERVICE_TOOL_LABELS["run_student_program"]
        assert body["status"] == "student_failure"
        assert body["code"] == "student_program_failed"
        assert body["exit_code"] == 1
        assert body["stdout"] == "资料条数：3\n"
        assert "NameError" in body["stderr"]
        assert body["location"] == {"file": "faq_app.py", "line": 7}
        assert body["snapshot"] == workbench["latest_snapshot"]["label"]
        assert body["recorded"] is True

        assert len(runtime.calls) == 1
        call = runtime.calls[0]
        assert call["event"]["requested_tool"] == "run_student_program"
        assert call["event"]["event_type"] == "run_tool"
        assert call["automatic_followup"] is False

        after = await client.get(f"/api/product/attempts/{attempt.id}/workbench", headers=headers)
        assert after.json()["attempt"]["student_failure_count"] == 0
        assert all(item["status"] == "NOT_RUN" for item in after.json()["requirements"])
    await app.state.business_engine.dispose()


@pytest.mark.parametrize("tool", ["run_faq_tests", "validate_retrieval", "shell"])
@pytest.mark.asyncio
async def test_program_run_rejects_tools_outside_the_self_service_list(tmp_path, tool):
    app, _manager, runtime, _service, attempt, _teacher, student, _other = await setup_app(tmp_path)
    headers = {"X-User-Id": student.id}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        workbench = await prepare_snapshot(client, attempt, headers)
        response = await client.post(
            f"/api/product/attempts/{attempt.id}/program-runs",
            headers=headers,
            json={
                "operation_id": "pr-run-bad",
                "snapshot_id": workbench["latest_snapshot"]["id"],
                "expected_state_version": workbench["attempt"]["state_version"],
                "tool": tool,
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "tool_not_allowed"
        assert runtime.calls == []
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_program_run_requires_the_owning_learner(tmp_path):
    app, _manager, runtime, _service, attempt, _teacher, _student, other = await setup_app(tmp_path)
    owner_headers = {"X-User-Id": "pr-student"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        workbench = await prepare_snapshot(client, attempt, owner_headers)
        response = await client.post(
            f"/api/product/attempts/{attempt.id}/program-runs",
            headers={"X-User-Id": other.id},
            json={
                "operation_id": "pr-run-other",
                "snapshot_id": workbench["latest_snapshot"]["id"],
                "expected_state_version": workbench["attempt"]["state_version"],
            },
        )
        assert response.status_code == 403
        assert runtime.calls == []
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_program_run_rejects_a_stale_snapshot(tmp_path):
    app, _manager, runtime, _service, attempt, _teacher, student, _other = await setup_app(tmp_path)
    headers = {"X-User-Id": student.id}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        workbench = await prepare_snapshot(client, attempt, headers)
        response = await client.post(
            f"/api/product/attempts/{attempt.id}/program-runs",
            headers=headers,
            json={
                "operation_id": "pr-run-stale",
                "snapshot_id": "snapshot-from-an-earlier-edit",
                "expected_state_version": workbench["attempt"]["state_version"],
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "old_snapshot"
        assert runtime.calls == []
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_workbench_exposes_the_server_owned_console_tools(tmp_path):
    app, _manager, _runtime, _service, attempt, _teacher, student, _other = await setup_app(tmp_path)
    headers = {"X-User-Id": student.id}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        workbench = await prepare_snapshot(client, attempt, headers)
        exposed = workbench["self_service_tools"]
        assert [item["name"] for item in exposed] == list(SELF_SERVICE_TOOLS)
        assert all(item["label"] for item in exposed)
        assert all(item["name"] in SELF_SERVICE_TOOL_LABELS for item in exposed)
        assert DEFAULT_TASK_POLICY["self_service_tools"] == list(SELF_SERVICE_TOOLS)
        graded = await client.post(
            f"/api/product/attempts/{attempt.id}/program-runs",
            headers=headers,
            json={
                "operation_id": "pr-run-extra",
                "snapshot_id": workbench["latest_snapshot"]["id"],
                "expected_state_version": workbench["attempt"]["state_version"],
                "formal_grade": 100,
            },
        )
        assert graded.status_code == 422
    await app.state.business_engine.dispose()


def test_every_self_service_tool_is_advisory():
    capabilities = DEFAULT_TASK_POLICY["tool_capabilities"]
    counted = set(DEFAULT_TASK_POLICY["failure_counting_capabilities"])
    for name in SELF_SERVICE_TOOLS:
        assert name in DEFAULT_TASK_POLICY["allowed_tools"]
        assert capabilities[name] not in counted
        assert name in SELF_SERVICE_TOOL_LABELS
