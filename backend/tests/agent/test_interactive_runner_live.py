"""A real Docker session must accept input after Python has started."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from app.agent.interactive_runner import InteractivePythonManager
from app.agent.runner import DockerPythonRunnerExecutor
from app.agent.workspace import AttemptWorkspaceManager

IMAGE = os.getenv("TEACHING_RUNNER_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set TEACHING_RUNNER_TEST_IMAGE for real Docker checks")


def test_running_program_accepts_two_separate_input_lines(tmp_path: Path) -> None:
    workspace = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    attempt_id, snapshot_id = "terminal-input", "snapshot-1"
    (workspace.source_directory(attempt_id) / "main.py").write_text(
        "first = int(input('第一个数：'))\nsecond = int(input('第二个数：'))\nprint(first + second)\n",
        encoding="utf-8",
    )
    workspace.create_snapshot(attempt_id=attempt_id, snapshot_id=snapshot_id)
    manager = InteractivePythonManager(DockerPythonRunnerExecutor(IMAGE, workspace))
    session = manager.start(
        operation_id="interactive-two-lines", attempt_id=attempt_id, owner_id="student-1",
        snapshot_id=snapshot_id, snapshot_label="版本 A",
    )
    terminal = manager.get(session["session_id"], attempt_id=attempt_id, owner_id="student-1")
    assert terminal is not None
    try:
        terminal.send_line("2")
        terminal.send_line("3")
        deadline = time.monotonic() + 20
        while terminal.snapshot()["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.1)
        result = terminal.snapshot()
        assert result["status"] == "completed"
        assert result["exit_code"] == 0
        assert [item["text"] for item in result["events"] if item["kind"] == "input"] == ["2\n", "3\n"]
        assert "5" in "".join(item["text"] for item in result["events"] if item["kind"] == "stdout")
        digest = hashlib.sha256(f"terminal:{session['session_id']}".encode()).hexdigest()
        artifact_path = workspace.evidence_directory(attempt_id) / f"{digest}.terminal.json"
        while not artifact_path.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["snapshot_id"] == snapshot_id
        assert artifact["input_lines"] == ["2", "3"]
    finally:
        terminal.stop()


def test_student_can_stop_program_waiting_for_input(tmp_path: Path) -> None:
    workspace = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    attempt_id, snapshot_id = "terminal-stop", "snapshot-1"
    (workspace.source_directory(attempt_id) / "main.py").write_text(
        "input('等待输入：')\n", encoding="utf-8",
    )
    workspace.create_snapshot(attempt_id=attempt_id, snapshot_id=snapshot_id)
    manager = InteractivePythonManager(DockerPythonRunnerExecutor(IMAGE, workspace))
    started = manager.start(
        operation_id="interactive-stop", attempt_id=attempt_id, owner_id="student-1",
        snapshot_id=snapshot_id, snapshot_label="版本 A",
    )
    terminal = manager.get(started["session_id"], attempt_id=attempt_id, owner_id="student-1")
    assert terminal is not None
    terminal.stop()
    deadline = time.monotonic() + 20
    while terminal.snapshot()["status"] == "stopping" and time.monotonic() < deadline:
        time.sleep(0.1)
    assert terminal.snapshot()["status"] == "stopped"
