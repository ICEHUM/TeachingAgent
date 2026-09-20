from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import tools
from app.agent.tools import OpenHandsExecutor, ToolPolicyError, ToolResultValidationError
from app.agent.workspace import AttemptWorkspaceManager
from app.teaching_control.protocols import ResourcePolicy, ToolExecutionRequest


class FakeRemoteWorkspace:
    def __init__(self, *, stdout: str, stderr: str = "", exit_code: int = 0, timeout=False):
        self.result = SimpleNamespace(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timeout_occurred=timeout,
        )
        self.calls = 0

    def execute_command(self, command: str, cwd: str, timeout: float):
        assert command and cwd == "/workspace/student" and timeout > 0
        self.calls += 1
        return self.result


def request(*, operation_id="tool:one", capability="EVALUATION", timeout=45):
    return ToolExecutionRequest(
        operation_id=operation_id,
        attempt_id="attempt-a",
        task_version="FAQ-001-v1",
        stage="implement_retrieval",
        snapshot_id="snapshot-1",
        tool_name="run_faq_tests",
        tool_capability=capability,
        timeout_seconds=timeout,
        resource_policy=ResourcePolicy(max_stdout_bytes=64, max_stderr_bytes=64),
    )


def install_workspace(monkeypatch, remote):
    @contextmanager
    def fake_workspace(**kwargs):
        del kwargs
        yield SimpleNamespace(workspace=remote, container_id="fake-container")

    monkeypatch.setattr(tools, "attempt_snapshot_workspace", fake_workspace)
    monkeypatch.setattr(
        tools,
        "container_security_facts",
        lambda container_id: {"container_id": container_id, "docker_socket_mounted": False},
    )


def test_real_executor_contract_persists_structured_evidence_and_replays_once(
    tmp_path: Path, monkeypatch
):
    payload = {
        "passed": False,
        "code": "empty_retrieval",
        "checks": [{"code": "known_question_hit", "passed": False}],
    }
    remote = FakeRemoteWorkspace(stdout=tools.MARKER + json.dumps(payload))
    install_workspace(monkeypatch, remote)
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    manager.source_directory("attempt-a").joinpath("faq_app.py").write_text("", encoding="utf-8")
    manager.create_snapshot(attempt_id="attempt-a", snapshot_id="snapshot-1")
    executor = OpenHandsExecutor(manager)

    first = executor.execute(request())
    replay = executor.execute(request())

    assert first.status == "student_failure"
    assert first.evidence[0].code == "empty_retrieval"
    assert first.evidence[0].snapshot_id == "snapshot-1"
    assert replay.duplicate is True
    assert remote.calls == 1


def test_timeout_is_infrastructure_failure_and_large_output_is_marked(
    tmp_path: Path, monkeypatch
):
    remote = FakeRemoteWorkspace(stdout="x" * 1024, stderr="y" * 1024, timeout=True)
    install_workspace(monkeypatch, remote)
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    manager.source_directory("attempt-a").joinpath("faq_app.py").write_text("", encoding="utf-8")
    manager.create_snapshot(attempt_id="attempt-a", snapshot_id="snapshot-1")

    result = OpenHandsExecutor(manager).execute(request())

    assert result.status == "infrastructure_failure"
    assert result.evidence[0].code == "execution_timeout"
    assert result.evidence[0].stdout_truncated is True
    assert result.evidence[0].stderr_truncated is True


def test_anomalous_structured_result_is_not_written(tmp_path: Path, monkeypatch):
    remote = FakeRemoteWorkspace(stdout="untrusted output", exit_code=0)
    install_workspace(monkeypatch, remote)
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    manager.source_directory("attempt-a").joinpath("faq_app.py").write_text("", encoding="utf-8")
    manager.create_snapshot(attempt_id="attempt-a", snapshot_id="snapshot-1")

    with pytest.raises(ToolResultValidationError):
        OpenHandsExecutor(manager).execute(request())
    assert not list((tmp_path / "evidence").rglob("*.result.json"))


def test_server_catalog_rejects_forged_capability_and_excess_timeout(tmp_path: Path):
    executor = OpenHandsExecutor(
        AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    )
    with pytest.raises(ToolPolicyError, match="capability"):
        executor.execute(request(capability="DIAGNOSTIC"))
    with pytest.raises(ToolPolicyError, match="timeout"):
        executor.execute(request(timeout=46))


def test_attempt_paths_are_isolated_and_traversal_is_rejected(tmp_path: Path):
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    a = manager.source_directory("student-a")
    b = manager.source_directory("student-b")
    a.joinpath("a.txt").write_text("a", encoding="utf-8")
    b.joinpath("private.txt").write_text("b", encoding="utf-8")
    snapshot, _ = manager.create_snapshot(attempt_id="student-a", snapshot_id="snapshot-a")

    assert snapshot != b
    assert not snapshot.joinpath("private.txt").exists()
    with pytest.raises(ValueError, match="unsafe"):
        manager.snapshot_directory("student-a", "../student-b")
