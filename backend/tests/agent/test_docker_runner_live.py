"""Opt-in real-container parity and isolation checks for the Python Runner."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.agent.runner import DockerPythonRunnerExecutor
from app.agent.tools import OpenHandsExecutor
from app.agent.workspace import AttemptWorkspaceManager, docker_command
from app.business.python_basics import TASK_PACKS
from app.teaching_control.protocols import ResourcePolicy, ToolExecutionRequest

IMAGE = os.getenv("TEACHING_RUNNER_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set TEACHING_RUNNER_TEST_IMAGE for real Docker checks")


def _request(attempt: str, snapshot: str, tool: str, backend: str, timeout: int = 45) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        operation_id=f"tool:parity:{backend}:{attempt}:{tool}",
        attempt_id=attempt,
        task_version="PYB-01-v1",
        stage="write_program",
        snapshot_id=snapshot,
        tool_name=tool,
        tool_capability="DIAGNOSTIC" if tool in {"run_python_sample", "run_python_trace"} else "EVALUATION",
        timeout_seconds=timeout,
        resource_policy=ResourcePolicy(),
    )


def _artifact(manager: AttemptWorkspaceManager, request: ToolExecutionRequest) -> dict:
    digest = hashlib.sha256(request.operation_id.encode()).hexdigest()
    return json.loads((manager.evidence_directory(request.attempt_id) / f"{digest}.artifact.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case,source,hidden_status", [
    ("correct", "first=int(input())\nsecond=int(input())\nprint(first+second)\n", "succeeded"),
    ("boundary", "first=int(input())\nsecond=int(input())\nprint(0 if first<0 or second<0 else first+second)\n", "student_failure"),
])
def test_runner_matches_openhands_on_same_snapshot_and_enforces_isolation(
    tmp_path: Path, case: str, source: str, hidden_status: str,
) -> None:
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    attempt = f"runner-{case}"
    snapshot = "snapshot-1"
    (manager.source_directory(attempt) / "main.py").write_text(source, encoding="utf-8")
    manager.create_snapshot(attempt_id=attempt, snapshot_id=snapshot)
    old = OpenHandsExecutor(manager)
    new = DockerPythonRunnerExecutor(IMAGE, manager)
    for tool, expected in (("run_python_public_tests", "succeeded"),
                           ("run_python_hidden_tests", hidden_status)):
        old_request = _request(attempt, snapshot, tool, "openhands")
        new_request = _request(attempt, snapshot, tool, "runner")
        old_result = old.execute(old_request)
        new_result = new.execute(new_request)
        assert old_result.status == new_result.status == expected
        assert old_result.executor_backend == "openhands"
        assert new_result.executor_backend == "docker_runner"
        assert new_result.evidence[0].snapshot_id == old_result.evidence[0].snapshot_id == snapshot
        old_payload = _artifact(manager, old_request)["details"]["structured"]
        new_artifact = _artifact(manager, new_request)
        assert new_artifact["executor_backend"] == "docker_runner"
        new_payload = new_artifact["details"]["structured"]
        assert [(check["code"], check["passed"]) for check in old_payload["checks"]] == [
            (check["code"], check["passed"]) for check in new_payload["checks"]
        ]
        facts = new_artifact["details"]["container_security"]
        assert facts["image_ref"] == IMAGE
        assert str(facts["image_id"]).startswith("sha256:")
        assert facts["container_user"] == "65534:65534"
        assert facts["network_mode"] == "none"
        assert facts["read_only_root"] is True
        assert facts["docker_socket_mounted"] is False
        assert facts["port_bindings"] == {}
        assert facts["memory_bytes"] == 1024 * 1024 * 1024
        assert facts["pids_limit"] == 128
        if tool == "run_python_hidden_tests":
            assert len(new_payload["checks"]) == 1
            assert new_payload["checks"][0]["code"] == "hidden_suite"
            assert all(value not in json.dumps(new_payload, ensure_ascii=False)
                       for value in ("-4", "789012", "912468"))


def test_runner_timeout_is_bounded_and_container_is_removed(tmp_path: Path) -> None:
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    attempt = "runner-timeout"
    snapshot = "snapshot-1"
    (manager.source_directory(attempt) / "main.py").write_text("while True: pass\n", encoding="utf-8")
    manager.create_snapshot(attempt_id=attempt, snapshot_id=snapshot)
    request = _request(attempt, snapshot, "run_python_sample", "runner", timeout=3)
    result = DockerPythonRunnerExecutor(IMAGE, manager).execute(request)
    assert result.status == "infrastructure_failure"
    assert result.evidence[0].code == "execution_timeout"
    containers = docker_command("ps", "--filter", "label=teachingagent.role=python-runner", "--format", "{{.ID}}")
    assert not containers.stdout.strip()


def test_runner_limits_actual_student_stdout(tmp_path: Path) -> None:
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    attempt = "runner-large-output"
    snapshot = "snapshot-1"
    (manager.source_directory(attempt) / "main.py").write_text("print('x' * 200000)\n", encoding="utf-8")
    manager.create_snapshot(attempt_id=attempt, snapshot_id=snapshot)
    request = _request(attempt, snapshot, "run_python_sample", "runner", timeout=20)
    result = DockerPythonRunnerExecutor(IMAGE, manager).execute(request)
    artifact = _artifact(manager, request)
    assert result.status == "succeeded"
    assert result.evidence[0].stdout_truncated is True
    assert len(artifact["stdout"].encode("utf-8")) <= request.resource_policy.max_stdout_bytes


@pytest.mark.parametrize("source,expected_status,expected_text", [
    ("first=int(input())\nsecond=int(input())\nprint(first+second)\n", "succeeded", "5"),
    ("first=int(input())\nsecond=int(input())\nprint(first/0)\n", "student_failure", "ZeroDivisionError"),
])
def test_debug_trace_uses_isolated_snapshot_and_returns_bounded_steps(
    tmp_path: Path, source: str, expected_status: str, expected_text: str,
) -> None:
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    attempt = "debug-" + hashlib.sha256(source.encode()).hexdigest()[:8]
    snapshot = "snapshot-1"
    (manager.source_directory(attempt) / "main.py").write_text(source, encoding="utf-8")
    manager.create_snapshot(attempt_id=attempt, snapshot_id=snapshot)
    request = _request(attempt, snapshot, "run_python_trace", "runner", timeout=30)
    result = DockerPythonRunnerExecutor(IMAGE, manager).execute(request)
    artifact = _artifact(manager, request)
    payload = artifact["details"]["structured"]
    assert result.status == expected_status
    assert expected_text in (payload["stdout"] + payload["stderr"])
    assert payload["trace"] and payload["trace"][0]["file"] == "main.py"
    assert any("first" in step["locals"] for step in payload["trace"][1:])
    assert len(artifact["stdout"].encode("utf-8")) <= request.resource_policy.max_stdout_bytes
    facts = artifact["details"]["container_security"]
    assert facts["network_mode"] == "none" and facts["read_only_root"] is True
    assert facts["docker_socket_mounted"] is False


def test_debug_trace_stays_parseable_with_long_output_and_loop(tmp_path: Path) -> None:
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    attempt = "debug-bounded"
    snapshot = "snapshot-1"
    (manager.source_directory(attempt) / "main.py").write_text(
        "for number in range(500):\n    pass\nprint('字' * 50000)\n", encoding="utf-8"
    )
    manager.create_snapshot(attempt_id=attempt, snapshot_id=snapshot)
    request = _request(attempt, snapshot, "run_python_trace", "runner", timeout=30)
    result = DockerPythonRunnerExecutor(IMAGE, manager).execute(request)
    artifact = _artifact(manager, request)
    payload = artifact["details"]["structured"]
    assert result.status == "succeeded"
    assert payload["trace_truncated"] is True
    assert payload["stdout_truncated"] is True
    assert len(artifact["stdout"].encode("utf-8")) <= request.resource_policy.max_stdout_bytes


@pytest.mark.parametrize("task_key,source,public_status,hidden_status", [
    ("PYB-02", "score=int(input())\nif score >= 60:\n    print('及格')\nelse:\n    print('不及格')\n", "succeeded", "succeeded"),
    ("PYB-02", "score=int(input())\nif score > 60:\n    print('及格')\nelse:\n    print('不及格')\n", "student_failure", None),
    ("PYB-03", "count=int(input())\nnumbers=[int(input()) for _ in range(count)]\ntotal=0\nfor number in numbers:\n    total+=number\nprint(total)\n", "succeeded", "succeeded"),
    ("PYB-03", "count=int(input())\nnumbers=[int(input()) for _ in range(count)]\ntotal=0\nseen=set()\nfor number in numbers:\n    if number not in seen:\n        total+=number\n        seen.add(number)\nprint(total)\n", "succeeded", "student_failure"),
    ("PYB-03", "count=int(input())\nnumbers=[int(input()) for _ in range(count)]\nprint(sum(numbers))\n", "student_failure", None),
])
def test_stage04_cases_in_real_runner(
    tmp_path: Path, task_key: str, source: str, public_status: str, hidden_status: str | None,
) -> None:
    pack = TASK_PACKS[task_key]
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    attempt = f"stage04-{task_key}-{hashlib.sha256(source.encode()).hexdigest()[:8]}"
    snapshot = "snapshot-1"
    (manager.source_directory(attempt) / "main.py").write_text(source, encoding="utf-8")
    manager.create_snapshot(attempt_id=attempt, snapshot_id=snapshot)
    runner = DockerPythonRunnerExecutor(IMAGE, manager)
    for tool, expected_status in (("run_python_public_tests", public_status),
                                  ("run_python_hidden_tests", hidden_status)):
        if expected_status is None:
            continue
        request = ToolExecutionRequest(
            operation_id=f"tool:stage04:{attempt}:{tool}", attempt_id=attempt,
            task_version=pack.task_version, stage="write_program", snapshot_id=snapshot,
            tool_name=tool, tool_capability="EVALUATION", timeout_seconds=45,
            resource_policy=ResourcePolicy(),
        )
        result = runner.execute(request)
        assert result.status == expected_status
        artifact = _artifact(manager, request)
        structured = artifact["details"]["structured"]
        if tool == "run_python_public_tests":
            assert structured["skill_evidence"]
            if task_key == "PYB-02" and expected_status == "student_failure":
                assert structured["checks"][1]["diagnosis_code"] == "boundary_condition"
            if "sum(numbers)" in source:
                assert any(item.get("diagnosis_code") == "missing_for_traversal"
                           for item in structured["checks"])
        else:
            assert structured["skill_evidence"] == []
            assert len(structured["checks"]) == 1
            assert structured["checks"][0]["code"] == "hidden_suite"
