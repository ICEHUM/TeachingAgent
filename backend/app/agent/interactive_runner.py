"""Short-lived, student-owned terminal sessions in the isolated Python Runner."""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.teaching_control.protocols import ResourcePolicy

from .runner import DockerPythonRunnerExecutor
from .workspace import AttemptWorkspaceManager, docker_cli_argv, docker_command

MAX_INPUT_BYTES = 4096
MAX_LINE_BYTES = 1024
MAX_OUTPUT_BYTES = 16_384
MAX_EVENTS = 512
SESSION_SECONDS = 180


class TerminalAlreadyRunning(Exception):
    pass


class TerminalNotRunning(Exception):
    pass


class TerminalInputLimit(Exception):
    pass


class InteractivePythonSession:
    def __init__(
        self, *, session_id: str, operation_id: str, attempt_id: str,
        owner_id: str, snapshot_id: str, snapshot_label: str,
        container_id: str, process: subprocess.Popen[bytes],
        workspace: AttemptWorkspaceManager,
    ) -> None:
        self.id = session_id
        self.operation_id = operation_id
        self.attempt_id = attempt_id
        self.owner_id = owner_id
        self.snapshot_id = snapshot_id
        self.snapshot_label = snapshot_label
        self.container_id = container_id
        self.process = process
        self.workspace = workspace
        self.created_at = datetime.now(UTC).isoformat()
        self.created_monotonic = time.monotonic()
        self.finished_monotonic: float | None = None
        self.status = "running"
        self.exit_code: int | None = None
        self.events: list[dict[str, Any]] = []
        self.input_lines: list[str] = []
        self.input_bytes = 0
        self.output_bytes = 0
        self.output_truncated = False
        self.stop_requested = False
        self.stdin_closed = False
        self._lock = threading.RLock()
        self._streams = [
            threading.Thread(target=self._read, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=self._read, args=(process.stderr, "stderr"), daemon=True),
        ]
        self._watcher = threading.Thread(target=self._watch, daemon=True)

    def start(self) -> None:
        for thread in self._streams:
            thread.start()
        self._watcher.start()

    def _append(self, kind: str, text: str) -> None:
        if not text or len(self.events) >= MAX_EVENTS:
            return
        if self.events and self.events[-1]["kind"] == kind and kind in {"stdout", "stderr"}:
            self.events[-1]["text"] += text
        else:
            self.events.append({"kind": kind, "text": text})

    def _read(self, pipe: Any, kind: str) -> None:
        if pipe is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while chunk := os.read(pipe.fileno(), 4096):
                with self._lock:
                    allowed = max(0, MAX_OUTPUT_BYTES - self.output_bytes)
                    kept = chunk[:allowed]
                    self.output_bytes += len(kept)
                    self._append(kind, decoder.decode(kept))
                    if len(kept) < len(chunk) and not self.output_truncated:
                        self.output_truncated = True
                        self._append("system", "输出超过 16 KB，后续内容已截断。")
            with self._lock:
                self._append(kind, decoder.decode(b"", final=True))
        except (OSError, ValueError):
            pass
        finally:
            pipe.close()

    def _watch(self) -> None:
        timed_out = False
        try:
            try:
                self.process.wait(timeout=SESSION_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                docker_command("rm", "--force", self.container_id, check=False)
                self.process.kill()
                self.process.wait(timeout=5)
            for thread in self._streams:
                thread.join(timeout=5)
            try:
                inspected = docker_command("inspect", "--format", "{{.State.ExitCode}}", self.container_id)
                exit_code = int(inspected.stdout.strip())
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                exit_code = self.process.returncode if self.process.returncode is not None else -1
            with self._lock:
                self.exit_code = exit_code
                self.status = "stopped" if self.stop_requested else "timed_out" if timed_out else "completed" if exit_code == 0 else "failed"
                self.finished_monotonic = time.monotonic()
                if timed_out:
                    self._append("system", "程序运行超过 3 分钟，已自动结束。")
        finally:
            try:
                docker_command("rm", "--force", self.container_id, check=False)
            finally:
                self._persist()

    def _persist(self) -> None:
        with self._lock:
            payload = {
                "session_id": self.id,
                "operation_id": self.operation_id,
                "attempt_id": self.attempt_id,
                "snapshot_id": self.snapshot_id,
                "snapshot_label": self.snapshot_label,
                "created_at": self.created_at,
                "status": self.status,
                "exit_code": self.exit_code,
                "input_lines": list(self.input_lines),
                "events": list(self.events),
                "output_truncated": self.output_truncated,
                "diagnostic_only": True,
            }
        digest = hashlib.sha256(f"terminal:{self.id}".encode()).hexdigest()
        path = self.workspace.evidence_directory(self.attempt_id) / f"{digest}.terminal.json"
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def send_line(self, line: str) -> dict[str, Any]:
        encoded = (line + "\n").encode("utf-8")
        if "\r" in line or "\n" in line or len(encoded) > MAX_LINE_BYTES:
            raise TerminalInputLimit()
        with self._lock:
            if self.status != "running" or self.stdin_closed or self.process.poll() is not None:
                raise TerminalNotRunning()
            if self.input_bytes + len(encoded) > MAX_INPUT_BYTES:
                raise TerminalInputLimit()
            if self.process.stdin is None:
                raise TerminalNotRunning()
            try:
                self.process.stdin.write(encoded)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise TerminalNotRunning() from exc
            self.input_lines.append(line)
            self.input_bytes += len(encoded)
            self._append("input", line + "\n")
        return self.snapshot()

    def close_input(self) -> dict[str, Any]:
        with self._lock:
            if self.status != "running" or self.stdin_closed:
                raise TerminalNotRunning()
            self.stdin_closed = True
            if self.process.stdin is not None:
                self.process.stdin.close()
            self._append("system", "输入已结束，等待程序退出。\n")
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self.status != "running":
                return self.snapshot()
            self.stop_requested = True
            self.status = "stopping"
        docker_command("rm", "--force", self.container_id, check=False)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.id,
                "snapshot_id": self.snapshot_id,
                "snapshot": self.snapshot_label,
                "status": self.status,
                "exit_code": self.exit_code,
                "events": [dict(event) for event in self.events],
                "output_truncated": self.output_truncated,
                "input_bytes": self.input_bytes,
                "stdin_closed": self.stdin_closed,
            }


class InteractivePythonManager:
    def __init__(self, runner: DockerPythonRunnerExecutor):
        self.runner = runner
        self.workspace = runner.manager
        self._lock = threading.RLock()
        self._sessions: dict[str, InteractivePythonSession] = {}
        self._operations: dict[str, str] = {}

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [session_id for session_id, session in self._sessions.items()
                   if session.finished_monotonic is not None and now - session.finished_monotonic > 600]
        for session_id in expired:
            operation_id = self._sessions[session_id].operation_id
            self._sessions.pop(session_id, None)
            self._operations.pop(operation_id, None)

    def start(self, *, operation_id: str, attempt_id: str, owner_id: str,
              snapshot_id: str, snapshot_label: str) -> dict[str, Any]:
        with self._lock:
            self._purge()
            previous_id = self._operations.get(operation_id)
            if previous_id:
                previous = self._sessions[previous_id]
                if previous.attempt_id == attempt_id and previous.owner_id == owner_id:
                    return previous.snapshot()
                raise TerminalAlreadyRunning()
            if any(session.attempt_id == attempt_id and session.status in {"running", "stopping"}
                   for session in self._sessions.values()):
                raise TerminalAlreadyRunning()
            source = self.workspace.snapshot_directory(attempt_id, snapshot_id)
            container_id, _facts = self.runner.create_container(
                source=source, limits=ResourcePolicy(), program_args=("/workspace/student/main.py",),
            )
            try:
                process = subprocess.Popen(
                    [*docker_cli_argv(), "start", "--attach", "--interactive", container_id],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0,
                )
            except Exception:
                docker_command("rm", "--force", container_id, check=False)
                raise
            session = InteractivePythonSession(
                session_id=str(uuid4()), operation_id=operation_id, attempt_id=attempt_id,
                owner_id=owner_id, snapshot_id=snapshot_id, snapshot_label=snapshot_label,
                container_id=container_id, process=process, workspace=self.workspace,
            )
            self._sessions[session.id] = session
            self._operations[operation_id] = session.id
            session.start()
            return session.snapshot()

    def get(self, session_id: str, *, attempt_id: str, owner_id: str) -> InteractivePythonSession | None:
        with self._lock:
            self._purge()
            session = self._sessions.get(session_id)
            return session if session and session.attempt_id == attempt_id and session.owner_id == owner_id else None

    def active(self, *, attempt_id: str, owner_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge()
            return next((session.snapshot() for session in self._sessions.values()
                         if session.attempt_id == attempt_id and session.owner_id == owner_id
                         and session.status in {"running", "stopping"}), None)

    def stop_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.stop()
