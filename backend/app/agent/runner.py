"""Docker CLI runner for versioned Python tasks, without an Agent Server call."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.business.python_basics import diagnose_public_case, structure_issue, task_pack_for
from app.teaching_control.protocols import ResourcePolicy, ToolExecutionRequest, ToolExecutionResult

from .python_debug import DEBUG_SCRIPT
from .tools import MARKER, OpenHandsExecutor, ToolPolicyError, ToolSpec
from .workspace import (
    AttemptWorkspaceManager,
    container_security_facts,
    docker_cli_argv,
    docker_command,
)

PINNED_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}$")
DEFAULT_PYTHON_RUNNER_IMAGE = (
    "python@"
    "sha256:1aaa65a85fda306ffb8b910824d4e93bdce61e212c7e87168123ea3073b41a1a"
)
MAX_CAPTURE_BYTES = 65_536


def _bounded_pipe(pipe: Any, limit: int, result: dict[str, Any], name: str) -> None:
    chunks: list[bytes] = []
    size = 0
    truncated = False
    while chunk := pipe.read(4096):
        kept = chunk[: max(0, limit - size)]
        if kept:
            chunks.append(kept)
            size += len(kept)
        if len(chunk) > len(kept):
            truncated = True
    result[name] = b"".join(chunks).decode("utf-8", errors="replace")
    result[name + "_truncated"] = truncated


class DockerPythonRunnerExecutor(OpenHandsExecutor):
    """Use an isolated non-root container for a server-owned Python task."""

    backend_name = "Docker Runner"
    backend_id = "docker_runner"

    def __init__(self, image: str, manager: AttemptWorkspaceManager | None = None):
        if not PINNED_IMAGE.fullmatch(image):
            raise ValueError("Runner image must use an immutable sha256 digest")
        super().__init__(manager)
        self.image = image

    def create_container(
        self, *, source: Path, limits: ResourcePolicy, program_args: tuple[str, ...],
    ) -> tuple[str, dict[str, object]]:
        if not source.is_dir():
            raise RuntimeError("Workspace snapshot does not exist")
        name = "teachingagent-runner-" + uuid.uuid4().hex
        created = docker_command(
            "create", "--name", name,
            "--label", "teachingagent.managed=true",
            "--label", "teachingagent.role=python-runner",
            "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--user", "65534:65534", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--memory", f"{limits.memory_mb}m",
            "--memory-swap", f"{limits.memory_mb}m",
            "--cpus", str(limits.cpu_count),
            "--pids-limit", str(limits.pids_limit),
            "--mount", f"type=bind,source={source},target=/workspace/student,readonly",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "PYTHONUNBUFFERED=1",
            "--interactive", "--entrypoint", "python", self.image,
            "-I", "-B", *program_args,
        )
        container_id = created.stdout.strip()
        if not container_id:
            raise RuntimeError("Docker did not return a container id")
        try:
            facts = container_security_facts(container_id)
            facts["image_ref"] = self.image
            if (
                facts["network_mode"] != "none"
                or facts["container_user"] != "65534:65534"
                or not facts["read_only_root"]
                or facts["docker_socket_mounted"]
                or bool(facts["port_bindings"])
                or facts["cap_drop"] != ["ALL"]
                or "no-new-privileges:true" not in facts["security_opt"]
                or not (0 < int(facts["memory_bytes"] or 0) <= limits.memory_mb * 1024 * 1024)
                or not (0 < int(facts["nano_cpus"] or 0) <= limits.cpu_count * 1_000_000_000)
                or not (0 < int(facts["pids_limit"] or 0) <= limits.pids_limit)
                or any(item["type"] == "bind" and item["destination"] != "/workspace/student"
                       for item in facts["mounts"])
                or any(item["read_write"] for item in facts["mounts"]
                       if item["destination"] == "/workspace/student")
            ):
                raise RuntimeError("Runner container security settings were not applied")
            return container_id, facts
        except Exception:
            docker_command("rm", "--force", container_id, check=False)
            raise

    def _run_once(
        self, *, source: Path, input_text: str, request: ToolExecutionRequest,
        debug: bool = False,
    ) -> tuple[SimpleNamespace, dict[str, object]]:
        program_args = ("-c", DEBUG_SCRIPT) if debug else ("/workspace/student/main.py",)
        container_id, facts = self.create_container(
            source=source, limits=request.resource_policy, program_args=program_args,
        )
        try:
            process = subprocess.Popen(
                [*docker_cli_argv(), "start", "--attach", "--interactive", container_id],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            capture: dict[str, Any] = {}
            assert process.stdout is not None and process.stderr is not None
            streams = [
                threading.Thread(target=_bounded_pipe, args=(process.stdout, MAX_CAPTURE_BYTES, capture, "stdout"), daemon=True),
                threading.Thread(target=_bounded_pipe, args=(process.stderr, MAX_CAPTURE_BYTES, capture, "stderr"), daemon=True),
            ]
            for stream in streams:
                stream.start()
            try:
                assert process.stdin is not None
                try:
                    process.stdin.write(input_text.encode("utf-8"))
                    process.stdin.close()
                except BrokenPipeError:
                    pass
                process.wait(timeout=request.timeout_seconds)
                timed_out = False
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                timed_out = True
            finally:
                for stream in streams:
                    stream.join(timeout=5)
            if timed_out:
                return SimpleNamespace(
                    stdout=capture.get("stdout", ""), stderr="程序运行超过时间限制",
                    exit_code=-1, timeout_occurred=True,
                    stdout_truncated=capture.get("stdout_truncated", False),
                    stderr_truncated=capture.get("stderr_truncated", False),
                ), facts
            inspected = docker_command("inspect", "--format", "{{.State.ExitCode}}", container_id)
            return SimpleNamespace(
                stdout=capture.get("stdout", ""), stderr=capture.get("stderr", ""),
                exit_code=int(inspected.stdout.strip()), timeout_occurred=False,
                stdout_truncated=capture.get("stdout_truncated", False),
                stderr_truncated=capture.get("stderr_truncated", False),
            ), facts
        finally:
            docker_command("rm", "--force", container_id, check=False)

    def _run_spec(self, request: ToolExecutionRequest, spec: ToolSpec) -> tuple[Any, dict[str, object]]:
        pack = task_pack_for(request.task_version)
        if pack is None:
            raise ToolPolicyError("Docker Python Runner only supports published Python basics packs")
        docker_command("image", "inspect", self.image, "--format", "{{.Id}}")
        source = self.manager.snapshot_directory(request.attempt_id, request.snapshot_id)
        if request.stdin_text is not None and (
            spec.name not in {"run_python_sample", "run_python_trace"}
            or len(request.stdin_text.encode("utf-8")) > 4096
        ):
            raise ToolPolicyError("Manual stdin is limited to Python diagnostic runs and 4 KB")
        if spec.name == "run_python_sample":
            return self._run_once(source=source, input_text=request.stdin_text if request.stdin_text is not None else pack.public_cases[0][1], request=request)
        if spec.name == "run_python_trace":
            return self._run_once(source=source, input_text=request.stdin_text if request.stdin_text is not None else pack.public_cases[0][1], request=request, debug=True)
        if not spec.cases:
            raise ToolPolicyError("Python Runner requires server-owned cases")
        checks: list[dict[str, Any]] = []
        errors: list[str] = []
        skill_evidence: list[dict[str, str]] = []
        security: dict[str, object] = {}
        for index, (_label, input_text, expected) in enumerate(spec.cases, 1):
            result, security = self._run_once(source=source, input_text=input_text, request=request)
            if result.timeout_occurred:
                return result, security
            actual = result.stdout.rstrip("\r\n")
            passed = result.exit_code == 0 and actual == expected and not result.stdout_truncated
            detail = (
                f"输入 {input_text.strip().replace(chr(10), ', ')}；预期 {expected}；实际 {actual or '(无输出)'}"
                if spec.reveal_cases else "边界用例已核对；具体输入在提交前不公开。"
            )
            check = {"code": f"case_{index}" if spec.reveal_cases else "hidden_case",
                     "passed": passed, "detail": detail}
            if spec.reveal_cases:
                check["diagnosis_code"] = diagnose_public_case(
                    task_key=pack.key, input_text=input_text, actual=actual,
                    expected=expected, stderr=result.stderr, exit_code=result.exit_code,
                    truncated=bool(result.stdout_truncated),
                )
                skill_evidence.append({
                    "skill_id": pack.public_case_skills[index - 1],
                    "status": "verified_in_sample" if passed else "needs_review",
                    "case_code": f"case_{index}",
                })
            checks.append(check)
            if not passed and result.stderr and spec.reveal_cases:
                errors.append(result.stderr[-1200:])
        if spec.reveal_cases:
            code = (source / "main.py").read_text(encoding="utf-8", errors="replace")
            issue = structure_issue(pack.key, code)
            if issue:
                detail = ("请使用 if/else 写出两个分支。" if issue == "missing_if_else"
                          else "请使用 for 遍历 numbers 列表并累加。")
                checks.append({"code": "structure_check", "passed": False,
                               "detail": detail, "diagnosis_code": issue})
                skill_evidence.append({"skill_id": pack.skills[0],
                                       "status": "needs_review", "case_code": "structure_check"})
        passed_all = all(item["passed"] for item in checks)
        if not spec.reveal_cases:
            checks = [{"code": "hidden_suite", "passed": passed_all,
                       "detail": "边界检查已完成；具体输入与预期结果不公开。"}]
        structured = {"passed": passed_all, "code": "passed" if passed_all else "check_failed",
                      "checks": checks, "error": "\n".join(errors)[-2400:],
                      "skill_evidence": skill_evidence}
        return SimpleNamespace(
            stdout=MARKER + json.dumps(structured, ensure_ascii=False), stderr="",
            exit_code=0 if passed_all else 2, timeout_occurred=False,
        ), security


class TaskRoutingExecutor:
    """Keep archived FAQ work on OpenHands while Python moves to Runner."""

    def __init__(self, *, legacy: OpenHandsExecutor, python: DockerPythonRunnerExecutor):
        if legacy.manager is not python.manager:
            raise ValueError("Task executors must use the same workspace manager")
        self.manager = legacy.manager
        self.legacy = legacy
        self.python = python

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        if task_pack_for(request.task_version) is not None:
            return self.python.execute(request)
        return self.legacy.execute(request)
