"""Runner dispatch and bounded capture without starting Docker."""

from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from app.agent.runner import (
    DEFAULT_PYTHON_RUNNER_IMAGE,
    DockerPythonRunnerExecutor,
    TaskRoutingExecutor,
    _bounded_pipe,
)
from app.agent.workspace import _docker_cli_argv
from app.main import create_app


def test_runner_rejects_mutable_image_tag() -> None:
    with pytest.raises(ValueError, match="immutable"):
        DockerPythonRunnerExecutor("python:3.12-slim")


def test_python_runner_is_default_and_can_be_explicitly_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHECKPOINT_DATABASE_URL", "postgresql://unused-local-checkpoint")
    monkeypatch.delenv("TEACHING_PYTHON_RUNNER_IMAGE", raising=False)
    app = create_app(database_url="sqlite+aiosqlite:///:memory:", sqlite_test_mode=True)
    executor = app.state.teaching_runtime.executor
    assert isinstance(executor, TaskRoutingExecutor)
    assert executor.python.image == DEFAULT_PYTHON_RUNNER_IMAGE
    monkeypatch.setenv("TEACHING_PYTHON_RUNNER_IMAGE", "")
    legacy_app = create_app(database_url="sqlite+aiosqlite:///:memory:", sqlite_test_mode=True)
    assert not isinstance(legacy_app.state.teaching_runtime.executor, TaskRoutingExecutor)


def test_capture_drains_large_output_but_keeps_only_bounded_prefix() -> None:
    captured: dict[str, object] = {}
    _bounded_pipe(BytesIO(b"x" * 200_000), 1024, captured, "stdout")
    assert len(str(captured["stdout"])) == 1024
    assert captured["stdout_truncated"] is True


def test_python_routes_to_runner_and_legacy_tasks_stay_on_openhands() -> None:
    calls: list[str] = []
    manager = object()
    legacy = SimpleNamespace(manager=manager, execute=lambda _request: calls.append("legacy"))
    python = SimpleNamespace(manager=manager, execute=lambda _request: calls.append("runner"))
    routing = TaskRoutingExecutor(legacy=legacy, python=python)
    assert routing.manager is manager
    routing.execute(SimpleNamespace(task_version="PYB-01-v1"))
    routing.execute(SimpleNamespace(task_version="FAQ-001-v1"))
    assert calls == ["runner", "legacy"]


def test_linux_runner_targets_only_an_absolute_unix_socket() -> None:
    assert _docker_cli_argv(PurePosixPath("/usr/bin/docker"), "posix", "/run/user/1000/docker.sock") == [
        "/usr/bin/docker", "--host", "unix:///run/user/1000/docker.sock",
    ]
    with pytest.raises(ValueError, match="absolute local Unix socket"):
        _docker_cli_argv(PurePosixPath("/usr/bin/docker"), "posix", "tcp://example.org:2375")
