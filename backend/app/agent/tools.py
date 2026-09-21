"""Server-owned FAQ-001-v1 tool catalog backed by OpenHands RemoteWorkspace."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.teaching_control.protocols import (
    ArtifactRef,
    EvidenceRecord,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from app.teaching_control.state import ToolCapability

from .workspace import (
    AttemptWorkspaceManager,
    WorkspaceSecurityPolicy,
    attempt_snapshot_workspace,
    container_security_facts,
    docker_command,
)

MARKER = "__TEACHING_EVIDENCE__="
MAX_SUMMARY = 512
STUDENT_EXEC_PREFIX = (
    "env -u LLM_API_KEY -u DEEPSEEK_API_KEY -u OPENAI_API_KEY "
    "-u ANTHROPIC_API_KEY -u OH_SESSION_API_KEYS_0 "
    "PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1"
)


class ToolPolicyError(RuntimeError):
    pass


class ToolResultValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    capability: ToolCapability
    timeout_seconds: int
    structured_result: bool
    requirement_kinds: tuple[str, ...]
    command: str


def _python_command(source: str) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return (
        f"{STUDENT_EXEC_PREFIX} python -I -c "
        f"\"import base64;exec(base64.b64decode('{encoded}'))\""
    )


INSPECT_WORKSPACE = r'''
import json
from pathlib import Path
root = Path("/workspace/student").resolve()
files = []
for item in sorted(root.rglob("*")):
    if item.is_symlink():
        raise RuntimeError("workspace_symlink_forbidden")
    if item.is_file():
        files.append({"path": item.relative_to(root).as_posix(), "size": item.stat().st_size})
        if len(files) >= 200:
            break
present = {item["path"] for item in files}
source_count = 0
try:
    sources = json.loads((root / "data/faq.json").read_text(encoding="utf-8"))
    source_count = len(sources) if isinstance(sources, list) else 0
except (OSError, json.JSONDecodeError):
    source_count = 0
checks = [
    {"code": "faq_program_present", "passed": "faq_app.py" in present},
    {"code": "faq_sources_present", "passed": "data/faq.json" in present},
    {"code": "minimum_three_sources", "passed": source_count >= 3},
]
payload = {
    "passed": all(item["passed"] for item in checks),
    "checks": checks,
    "files": files,
    "source_count": source_count,
}
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


FAQ_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path

program = Path("/workspace/student/faq_app.py")
source_path = Path("/workspace/student/data/faq.json")
checks = []
error = ""
try:
    spec = importlib.util.spec_from_file_location("student_faq_app", program)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = module.load_sources(str(source_path))
    checks.append({"code": "sources_loaded", "passed": isinstance(sources, list) and len(sources) >= 2})
    known = module.retrieve("如何重置密码？", sources)
    checks.append({"code": "known_question_hit", "passed": isinstance(known, list) and len(known) > 0})
    unknown = module.retrieve("火星基地的食堂今天供应什么？", sources)
    checks.append({"code": "unknown_question_no_fabrication", "passed": isinstance(unknown, list) and len(unknown) == 0})
    citation_ok = bool(known) and all(
        isinstance(item, dict) and bool(item.get("source") or item.get("url") or item.get("citation"))
        for item in known
    )
    checks.append({"code": "basic_citation_present", "passed": citation_ok})
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
failed = [item["code"] for item in checks if not item["passed"]]
code = "passed" if not failed else ("empty_retrieval" if "known_question_hit" in failed else failed[0])
payload = {"passed": not failed, "code": code, "checks": checks, "error": error}
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


RETRIEVAL_TESTS = FAQ_TESTS


CITATION_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path
spec = importlib.util.spec_from_file_location("student_faq_app", Path("/workspace/student/faq_app.py"))
module = importlib.util.module_from_spec(spec)
checks = []
error = ""
try:
    spec.loader.exec_module(module)
    sources = module.load_sources("/workspace/student/data/faq.json")
    hits = module.retrieve("如何重置密码？", sources)
    passed = bool(hits) and all(
        isinstance(item, dict) and bool(item.get("source") or item.get("url") or item.get("citation"))
        for item in hits
    )
    checks.append({"code": "basic_citation_present", "passed": passed})
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
payload = {"passed": all(item["passed"] for item in checks), "checks": checks, "error": error}
payload["code"] = "passed" if payload["passed"] else checks[0]["code"]
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


POLICY_FAQ_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path

program = Path("/workspace/student/faq_app.py")
source_path = Path("/workspace/student/data/faq.json")
checks = []
error = ""
try:
    spec = importlib.util.spec_from_file_location("student_policy_faq", program)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = module.load_sources(str(source_path))
    checks.append({"code": "sources_loaded", "passed": isinstance(sources, list) and len(sources) >= 3})
    known = module.retrieve("实习单位可以安排学生上夜班吗？", sources)
    checks.append({"code": "known_question_hit", "passed": isinstance(known, list) and len(known) > 0})
    unknown = module.retrieve("火星基地的学生实习补贴是多少？", sources)
    checks.append({"code": "unknown_question_no_fabrication", "passed": isinstance(unknown, list) and len(unknown) == 0})
    citation_ok = bool(known) and all(
        isinstance(item, dict)
        and bool(item.get("source"))
        and bool(item.get("authority"))
        and bool(item.get("scope"))
        for item in known
    )
    checks.append({"code": "authoritative_citation_present", "passed": citation_ok})
    local = module.retrieve("江苏省内职业学校如何落实学生实习管理？", sources)
    local_scope_ok = bool(local) and any("江苏" in str(item.get("scope", "")) for item in local)
    checks.append({"code": "local_rule_scope_preserved", "passed": local_scope_ok})
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
failed = [item["code"] for item in checks if not item["passed"]]
code = "passed" if not failed else ("empty_retrieval" if "known_question_hit" in failed else failed[0])
payload = {"passed": not failed, "code": code, "checks": checks, "error": error}
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


POLICY_CITATION_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path

spec = importlib.util.spec_from_file_location("student_policy_faq", Path("/workspace/student/faq_app.py"))
module = importlib.util.module_from_spec(spec)
checks = []
error = ""
try:
    spec.loader.exec_module(module)
    sources = module.load_sources("/workspace/student/data/faq.json")
    hits = module.retrieve("学校可以强制学生到指定企业实习吗？", sources)
    citation_ok = bool(hits) and all(
        isinstance(item, dict)
        and bool(item.get("source"))
        and bool(item.get("authority"))
        and bool(item.get("scope"))
        for item in hits
    )
    checks.append({"code": "authoritative_citation_present", "passed": citation_ok})
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
payload = {"passed": all(item["passed"] for item in checks), "checks": checks, "error": error}
payload["code"] = "passed" if payload["passed"] else checks[0]["code"]
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


TOOL_CATALOG: dict[str, ToolSpec] = {
    "inspect_workspace": ToolSpec(
        "inspect_workspace",
        "DIAGNOSTIC",
        30,
        True,
        ("STATIC_CHECK",),
        _python_command(INSPECT_WORKSPACE),
    ),
    "run_student_program": ToolSpec(
        "run_student_program",
        "DIAGNOSTIC",
        30,
        False,
        (),
        f"{STUDENT_EXEC_PREFIX} python /workspace/student/faq_app.py",
    ),
    "run_faq_tests": ToolSpec(
        "run_faq_tests",
        "EVALUATION",
        45,
        True,
        ("AUTO_TEST",),
        _python_command(FAQ_TESTS),
    ),
    "validate_retrieval": ToolSpec(
        "validate_retrieval",
        "EVALUATION",
        45,
        True,
        ("AUTO_TEST",),
        _python_command(RETRIEVAL_TESTS),
    ),
    "validate_citations": ToolSpec(
        "validate_citations",
        "EVALUATION",
        45,
        True,
        ("STATIC_CHECK", "AUTO_TEST"),
        _python_command(CITATION_TESTS),
    ),
    "inspect_runtime_error": ToolSpec(
        "inspect_runtime_error",
        "DIAGNOSTIC",
        30,
        False,
        ("STATIC_CHECK",),
        _python_command(
            "from pathlib import Path; "
            "path=Path('/workspace/student/faq_app.py'); "
            "compile(path.read_text(encoding='utf-8'), str(path), 'exec')"
        ),
    ),
}


POLICY_TOOL_CATALOG: dict[str, ToolSpec] = {
    **TOOL_CATALOG,
    "run_faq_tests": ToolSpec(
        "run_faq_tests", "EVALUATION", 45, True, ("AUTO_TEST",),
        _python_command(POLICY_FAQ_TESTS),
    ),
    "validate_retrieval": ToolSpec(
        "validate_retrieval", "EVALUATION", 45, True, ("AUTO_TEST",),
        _python_command(POLICY_FAQ_TESTS),
    ),
    "validate_citations": ToolSpec(
        "validate_citations", "EVALUATION", 45, True, ("STATIC_CHECK", "AUTO_TEST"),
        _python_command(POLICY_CITATION_TESTS),
    ),
}


def tool_catalog_for(task_version: str) -> dict[str, ToolSpec]:
    if task_version == "POLICY-FAQ-001-v1":
        return POLICY_TOOL_CATALOG
    if task_version == "FAQ-001-v1":
        return TOOL_CATALOG
    raise ToolPolicyError(f"unsupported task version: {task_version}")


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _structured_payload(stdout: str) -> dict[str, Any]:
    candidates = [line[len(MARKER) :] for line in stdout.splitlines() if line.startswith(MARKER)]
    if len(candidates) != 1:
        raise ToolResultValidationError("expected exactly one structured evidence marker")
    try:
        payload = json.loads(candidates[0])
    except json.JSONDecodeError as exc:
        raise ToolResultValidationError("structured evidence is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("passed"), bool):
        raise ToolResultValidationError("structured evidence has an invalid schema")
    checks = payload.get("checks", [])
    if not isinstance(checks, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("code"), str)
        and isinstance(item.get("passed"), bool)
        for item in checks
    ):
        raise ToolResultValidationError("structured checks have an invalid schema")
    return payload


def validate_execution_result(
    request: ToolExecutionRequest, result: ToolExecutionResult
) -> None:
    if result.operation_id != request.operation_id or not result.evidence:
        raise ToolResultValidationError("tool result binding is incomplete")
    for evidence in result.evidence:
        expected = (
            evidence.attempt_id == request.attempt_id
            and evidence.task_version == request.task_version
            and evidence.snapshot_id == request.snapshot_id
            and evidence.operation_id == request.operation_id
            and evidence.tool_name == request.tool_name
            and evidence.status == result.status
            and bool(evidence.artifact_refs)
        )
        if not expected:
            raise ToolResultValidationError("evidence binding does not match the request")


class OpenHandsExecutor:
    """Executes only the fixed FAQ tool catalog in an isolated OpenHands workspace."""

    def __init__(self, manager: AttemptWorkspaceManager | None = None):
        self.manager = manager or AttemptWorkspaceManager()

    def _paths(self, request: ToolExecutionRequest) -> tuple[Path, Path]:
        digest = hashlib.sha256(request.operation_id.encode("utf-8")).hexdigest()
        directory = self.manager.evidence_directory(request.attempt_id)
        return directory / f"{digest}.result.json", directory / f"{digest}.artifact.json"

    def _load_duplicate(self, request: ToolExecutionRequest, path: Path) -> ToolExecutionResult:
        payload = json.loads(path.read_text(encoding="utf-8"))
        evidence_items = []
        for item in payload["evidence"]:
            item = dict(item)
            artifacts = tuple(ArtifactRef(**artifact) for artifact in item.pop("artifact_refs"))
            observed_at = datetime.fromisoformat(item.pop("observed_at"))
            evidence_items.append(
                EvidenceRecord(
                    **item,
                    artifact_refs=artifacts,
                    observed_at=observed_at,
                )
            )
        result = ToolExecutionResult(
            operation_id=payload["operation_id"],
            status=payload["status"],
            output_ref=payload["output_ref"],
            summary=payload["summary"],
            evidence=tuple(evidence_items),
            duplicate=True,
        )
        validate_execution_result(request, result)
        return result

    def _persist(
        self,
        request: ToolExecutionRequest,
        *,
        status: Literal["succeeded", "student_failure", "infrastructure_failure"],
        code: str,
        summary: str,
        stdout: str,
        stderr: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
        details: dict[str, Any],
        result_path: Path,
        artifact_path: Path,
    ) -> ToolExecutionResult:
        artifact_payload = {
            "request": {
                **asdict(request),
                "resource_policy": asdict(request.resource_policy),
            },
            "status": status,
            "code": code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "details": details,
        }
        encoded = json.dumps(
            artifact_payload, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        artifact_path.write_bytes(encoded)
        artifact_digest = hashlib.sha256(encoded).hexdigest()
        operation_digest = hashlib.sha256(request.operation_id.encode("utf-8")).hexdigest()
        attempt_digest = hashlib.sha256(request.attempt_id.encode("utf-8")).hexdigest()[:24]
        artifact = ArtifactRef(
            uri=f"artifact://{attempt_digest}/{operation_digest}",
            kind="evidence_manifest",
            sha256=artifact_digest,
            size_bytes=len(encoded),
        )
        evidence_id = hashlib.sha256(
            f"{request.attempt_id}:{request.snapshot_id}:{request.operation_id}".encode()
        ).hexdigest()
        observed_at = datetime.now(UTC)
        evidence = EvidenceRecord(
            evidence_id=evidence_id,
            attempt_id=request.attempt_id,
            task_version=request.task_version,
            snapshot_id=request.snapshot_id,
            operation_id=request.operation_id,
            tool_name=request.tool_name,
            status=status,
            code=code,
            summary=summary[:MAX_SUMMARY],
            artifact_refs=(artifact,),
            observed_at=observed_at,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        result = ToolExecutionResult(
            operation_id=request.operation_id,
            status=status,
            output_ref=f"evidence://{attempt_digest}/{evidence_id}",
            summary=summary[:MAX_SUMMARY],
            evidence=(evidence,),
        )
        validate_execution_result(request, result)
        serialized = asdict(result)
        result_path.write_text(
            json.dumps(serialized, ensure_ascii=False, sort_keys=True, default=str),
            encoding="utf-8",
        )
        return result

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        spec = tool_catalog_for(request.task_version).get(request.tool_name)
        if spec is None:
            raise ToolPolicyError("tool is not in the server-owned task catalog")
        if request.tool_capability != spec.capability:
            raise ToolPolicyError("client capability does not match the server tool catalog")
        if request.timeout_seconds <= 0 or request.timeout_seconds > spec.timeout_seconds:
            raise ToolPolicyError("tool timeout exceeds the server policy")
        if request.resource_policy.network_policy != "internal_only":
            raise ToolPolicyError("FAQ tools require the internal-only network policy")
        result_path, artifact_path = self._paths(request)
        if result_path.exists():
            return self._load_duplicate(request, result_path)
        lock_path = result_path.with_suffix(".lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
        except FileExistsError as exc:
            if result_path.exists():
                return self._load_duplicate(request, result_path)
            raise ToolPolicyError("operation is already running") from exc
        try:
            stdout = ""
            stderr = ""
            details: dict[str, Any] = {}
            try:
                policy = WorkspaceSecurityPolicy(
                    cpu_count=request.resource_policy.cpu_count,
                    memory_mb=request.resource_policy.memory_mb,
                    pids_limit=request.resource_policy.pids_limit,
                )
                with attempt_snapshot_workspace(
                    manager=self.manager,
                    attempt_id=request.attempt_id,
                    snapshot_id=request.snapshot_id,
                    policy=policy,
                ) as running:
                    security_facts = container_security_facts(running.container_id)
                    command_result = running.workspace.execute_command(
                        spec.command,
                        cwd="/workspace/student",
                        timeout=float(request.timeout_seconds),
                    )
                    if command_result.exit_code == -1:
                        logs = docker_command(
                            "logs", "--tail", "20", running.container_id, check=False
                        )
                        command_result.stderr += "\nAgent Server logs:\n" + (
                            logs.stdout + logs.stderr
                        )[-4096:]
                stdout, stdout_truncated = _truncate(
                    command_result.stdout, request.resource_policy.max_stdout_bytes
                )
                stderr, stderr_truncated = _truncate(
                    command_result.stderr, request.resource_policy.max_stderr_bytes
                )
                details = {"container_security": security_facts}
                if command_result.timeout_occurred:
                    status = "infrastructure_failure"
                    code = "execution_timeout"
                    summary = "OpenHands terminated the tool after the server timeout."
                elif spec.structured_result:
                    try:
                        structured = _structured_payload(command_result.stdout)
                    except ToolResultValidationError as exc:
                        bounded_error, _ = _truncate(command_result.stderr, 1024)
                        raise ToolResultValidationError(
                            f"{exc}; exit_code={command_result.exit_code}; "
                            f"stderr={bounded_error!r}"
                        ) from exc
                    details = {
                        "structured": structured,
                        "container_security": security_facts,
                    }
                    status = "succeeded" if structured["passed"] else "student_failure"
                    code = str(
                        structured.get("code")
                        or ("passed" if structured["passed"] else "check_failed")
                    )
                    summary = (
                        "FAQ validation passed."
                        if status == "succeeded"
                        else f"FAQ validation failed: {code}."
                    )
                elif command_result.exit_code == 0:
                    status = "succeeded"
                    code = "passed"
                    summary = f"{request.tool_name} completed successfully."
                else:
                    status = "student_failure"
                    code = "student_program_failed"
                    summary = f"{request.tool_name} returned a non-zero exit code."
            except ToolResultValidationError:
                raise
            # This is the infrastructure boundary: Docker, HTTP transport, and
            # Agent Server startup failures are deliberately normalized here.
            except Exception as exc:  # noqa: BLE001
                stdout_truncated = False
                stderr_truncated = False
                status = "infrastructure_failure"
                code = "workspace_start_failed"
                summary = f"OpenHands workspace execution failed: {type(exc).__name__}."
                details = {
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[-4096:],
                }
            return self._persist(
                request,
                status=status,
                code=code,
                summary=summary,
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                details=details,
                result_path=result_path,
                artifact_path=artifact_path,
            )
        finally:
            lock_path.unlink(missing_ok=True)


def with_duplicate(result: ToolExecutionResult) -> ToolExecutionResult:
    """Small public helper used by protocol tests."""

    return replace(result, duplicate=True)
