"""Stage 05A product views over the existing business facts and teaching runtime."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.interactive_runner import (
    InteractivePythonManager,
    TerminalAlreadyRunning,
    TerminalInputLimit,
    TerminalNotRunning,
)
from app.agent.workspace import AttemptWorkspaceManager, _tree_digest
from app.teaching_control.state import TeachingEvent as GraphEvent

from .api import current_user, db_session, fail
from .faq import DEFAULT_TASK_POLICY
from .models import (
    Attempt,
    Course,
    CourseMembership,
    FormalGrade,
    Intervention,
    OperationLedger,
    RequirementDefinition,
    RequirementResult,
    Review,
    Snapshot,
    Submission,
    Task,
    TaskStage,
    TaskVersion,
    TeachingEvent,
    User,
)
from .python_basics import POLICY as PYB01_POLICY
from .python_basics import task_pack_for
from .reviews import ReviewService
from .service import BusinessRuleError, BusinessService

router = APIRouter(prefix="/api/product", tags=["stage05a-product"])
logger = logging.getLogger(__name__)
service = BusinessService()
reviews = ReviewService(service)

# Server-owned list for the student console. The teaching runtime forces this list from
# the same task policy, so an advisory run can never reach a graded tool.
SELF_SERVICE_TOOLS = tuple(DEFAULT_TASK_POLICY["self_service_tools"])
SELF_SERVICE_TOOL_LABELS = {
    "run_student_program": "运行我的程序",
    "inspect_runtime_error": "检查语法错误",
    "run_python_sample": "试运行样例 2 与 3",
    "run_python_trace": "逐行调试公开样例",
}
CONSOLE_STDOUT_CHARS = 8192
CONSOLE_STDERR_CHARS = 4096

REQUIREMENT_NAMES = {
    "addition_public_tests": "公开样例检查",
    "addition_hidden_tests": "提交边界检查",
    "passing_public_tests": "公开样例检查",
    "passing_hidden_tests": "提交边界检查",
    "traversal_public_tests": "公开样例检查",
    "traversal_hidden_tests": "提交边界检查",
    "explain_scope": "说明任务边界",
    "identify_citation_rule": "识别引用规则",
    "source_manifest": "资料正常加载",
    "source_quality_review": "资料质量复核",
    "retrieval_public_tests": "已知问题能够命中",
    "retrieval_observation": "已提交调试观察",
    "answer_public_tests": "实现 answer()，回答正文来自命中资料",
    "citation_static_check": "返回 citations 和适用范围 scope",
    "unknown_question_test": "未知问题不伪造命中",
    "boundary_transfer": "完成边界迁移任务",
    "delivery_static_check": "交付文件完整",
    "delivery_review": "教师完成交付复核",
}
REQUIREMENT_STUDENT_GUIDANCE = {
    "addition_public_tests": {"goal": "读取两行整数并只输出相加结果。", "success": "公开样例 2+3 和 0+7 都正确。"},
    "addition_hidden_tests": {"goal": "提交时验证同一程序能处理题面范围内的其他整数。", "success": "边界用例均通过。"},
    "passing_public_tests": {"goal": "用 if/else 判断分数是否达到 60 分。", "success": "59 与 60 的公开样例均通过。"},
    "passing_hidden_tests": {"goal": "提交时验证 0–100 范围内的其他分数。", "success": "边界用例均通过。"},
    "traversal_public_tests": {"goal": "遍历 numbers，累加并输出总和。", "success": "三个数与空列表的公开样例均通过。"},
    "traversal_hidden_tests": {"goal": "提交时验证不同长度和整数的列表。", "success": "边界用例均通过。"},
    "answer_public_tests": {
        "goal": "新增 answer(question, sources)，先调用 retrieve()，再使用首条命中资料的 answer。",
        "success": "返回字典中的 answer 与命中资料一致；没有命中时明确说明资料不足。",
    },
    "citation_static_check": {
        "goal": "在返回字典中加入 citations 和 scope。",
        "success": "citations[0] 包含 title、url、authority；scope 保留资料适用范围。",
    },
}
REQUIREMENT_DISPLAY_ORDER = {
    key: index
    for index, key in enumerate(
        (
            "explain_scope",
            "identify_citation_rule",
            "source_manifest",
            "source_quality_review",
            "retrieval_public_tests",
            "retrieval_observation",
            "answer_public_tests",
            "citation_static_check",
            "unknown_question_test",
            "boundary_transfer",
            "delivery_static_check",
            "delivery_review",
        )
    )
}
STAGE_CHECK_TOOLS = {
    "write_program": ("run_python_public_tests",),
    "understand_requirements": ("inspect_task_brief",),
    "prepare_sources": ("inspect_workspace",),
    "implement_retrieval": ("run_faq_tests",),
    "validate_boundaries": ("run_faq_tests", "validate_transfer"),
    "deliver": ("inspect_delivery",),
    "generate_cited_answer": ("validate_answer_citations", "validate_answer"),
}
STAGE_OBJECTIVES = {
    "understand_requirements": "明确校园服务问答要解决的问题，并说明回答必须来自资料、保留来源。",
    "prepare_sources": "准备三条结构清晰、可追溯的校园服务资料。",
    "implement_retrieval": "让密码重置、实训室开放等已知问题命中资料，同时让资料外问题保持无结果。",
    "generate_cited_answer": "在 faq_app.py 新增 answer(question, sources)：回答正文取自命中资料，并保留来源标题、链接和适用范围。",
    "validate_boundaries": "验证资料外问题明确返回无法回答，不凭空编造答案。",
    "deliver": "整理运行说明与可复现的校园服务问答交付物。",
}
ALLOWED_FILE_SUFFIXES = {".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}
MAX_FILE_BYTES = 512 * 1024


class FileSave(BaseModel):
    content: str = Field(max_length=MAX_FILE_BYTES)
    expected_hash: str


class FileMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_path: str = Field(min_length=1, max_length=240)
    expected_hash: str


class FileDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_hash: str


class SnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    expected_file_hash: str | None = None
    expected_file_path: str = Field(default="faq_app.py", min_length=1, max_length=240)
    reuse_unchanged: bool = False


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    snapshot_id: str
    expected_state_version: int
    observation: str = Field(default="", max_length=1200)


class ProgramRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    snapshot_id: str
    expected_state_version: int
    tool: str = "run_student_program"
    stdin_text: str | None = Field(default=None, max_length=4096)


class TerminalStartCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    snapshot_id: str
    expected_state_version: int


class TerminalInputCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line: str = Field(max_length=1024)


class GuidanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    snapshot_id: str
    expected_state_version: int
    observation: str = Field(default="", max_length=1200)


class TeacherAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    expected_state_version: int
    action: str
    teacher_prompt: str = Field(default="", max_length=1200)


class SubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    snapshot_id: str
    explanation: str = Field(default="", max_length=2000)


class ReviewDraftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    score: int | None = None
    reason: str = Field(default="", max_length=800)
    confirmed: bool = False


class ReviewDraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ReviewDraftItem]


class ReviewPublish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    change_reason: str = Field(default="", max_length=800)


class TeacherRequirementReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    requirement_key: str
    status: str
    reason: str = Field(min_length=4, max_length=800)


def _manager(request: Request) -> AttemptWorkspaceManager:
    return getattr(request.app.state, "workspace_manager", AttemptWorkspaceManager())


def _runtime(request: Request):
    runtime = getattr(request.app.state, "teaching_runtime", None)
    if runtime is None:
        raise HTTPException(503, detail={"code": "runtime_unavailable", "message": "实训执行服务暂不可用"})
    return runtime


async def _attempt(
    session: AsyncSession,
    *,
    attempt_id: str,
    user: User,
) -> Attempt:
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "attempt_not_found")
    try:
        await service.authorize_attempt(
            session,
            attempt=attempt,
            user_id=user.id,
            teacher=attempt.learner_id != user.id,
        )
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    return attempt


def _relative_file(root: Path, raw_path: str) -> Path:
    normalized = raw_path.replace("\\", "/")
    value = PurePosixPath(normalized)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        raise HTTPException(400, detail={"code": "path_traversal", "message": "文件路径不在当前实训目录中"})
    if value.suffix.lower() not in ALLOWED_FILE_SUFFIXES:
        raise HTTPException(400, detail={"code": "file_type_forbidden", "message": "该文件类型不可在线编辑"})
    candidate = root.joinpath(*value.parts)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "path_traversal", "message": "文件路径不在当前实训目录中"}) from exc
    if candidate.exists() and candidate.is_symlink():
        raise HTTPException(400, detail={"code": "workspace_symlink_forbidden", "message": "不允许编辑符号链接"})
    return candidate


def _file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _snapshot_label(snapshot: Snapshot | None) -> str | None:
    return f"Snapshot {chr(64 + snapshot.sequence)}" if snapshot and 0 < snapshot.sequence <= 26 else (
        f"Snapshot {snapshot.sequence}" if snapshot else None
    )


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "event_type", "decision", "stage", "snapshot_id", "evidence_refs", "check_status",
        "help_level", "student_failure_count", "infrastructure_failure_count", "actor_role",
        "student_observation", "guidance", "from_stage", "to_stage", "assessment_reason",
    }
    clean = {key: payload[key] for key in allowed if key in payload}
    guidance = clean.get("guidance")
    if isinstance(guidance, dict):
        clean["guidance"] = {
            key: guidance.get(key)
            for key in (
                "level", "message", "evidence_refs", "next_step", "uncertainty", "provider",
                "model", "latency_ms", "success", "fallback_reason",
            )
        }
        if guidance.get("success") is not True:
            clean["guidance"].update(message="", next_step="", evidence_refs=[])
    return clean


def _event_label(event: TeachingEvent) -> str:
    payload = event.payload or {}
    event_type = payload.get("event_type") or event.event_type
    decision = payload.get("decision")
    if event_type == "help_requested":
        return "学生请求教师帮助"
    if event_type == "teacher_feedback":
        return "教师已给出指导"
    if event_type == "stage_advanced":
        return "阶段验收通过，进入下一阶段"
    if decision == "execute_tool":
        status = payload.get("check_status")
        if payload.get("tool_name") in (*SELF_SERVICE_TOOLS, *PYB01_POLICY["self_service_tools"]):
            return {
                "passed": "学生自己运行程序（正常结束）",
                "student_failure": "学生自己运行程序（报错）",
                "infrastructure_failure": "学生自运行时环境异常",
            }.get(status, "学生自己运行程序")
        return {"passed": "运行检查通过", "student_failure": "运行检查未通过", "infrastructure_failure": "运行环境异常"}.get(status, "运行实训检查")
    if decision == "generate_guidance":
        level = payload.get("help_level")
        return {"L0": "生成引导", "L1": "生成定位指导", "L2": "生成局部示例"}.get(level, "生成教学指导")
    if decision == "teacher_interrupt":
        return "等待教师介入"
    if payload.get("student_observation"):
        return "学生提交调试观察"
    return "记录教学事件"


async def _workbench_view(
    session: AsyncSession,
    *,
    attempt: Attempt,
    manager: AttemptWorkspaceManager,
) -> dict[str, Any]:
    version = await session.get(TaskVersion, attempt.task_version_id)
    task = await session.get(Task, version.task_id) if version else None
    course = await session.get(Course, task.course_id) if task else None
    learner = await session.get(User, attempt.learner_id)
    current_stage = await session.get(TaskStage, attempt.current_stage_id)
    if not all((version, task, course, learner, current_stage)):
        raise HTTPException(500, "attempt_context_incomplete")
    stages = list((await session.scalars(
        select(TaskStage).where(TaskStage.task_version_id == version.id).order_by(TaskStage.position)
    )).all())
    snapshots = list((await session.scalars(
        select(Snapshot).where(Snapshot.attempt_id == attempt.id).order_by(Snapshot.sequence)
    )).all())
    latest_snapshot = snapshots[-1] if snapshots else None
    definitions = list((await session.scalars(
        select(RequirementDefinition)
        .where(RequirementDefinition.task_stage_id == current_stage.id)
    )).all())
    pack = task_pack_for(f"{task.task_key}-{version.version}")
    definitions.sort(key=lambda item: (0 if item.requirement_key == pack.public_requirement else 1)
                     if pack is not None else REQUIREMENT_DISPLAY_ORDER.get(item.requirement_key, 999))
    results = list((await session.scalars(
        select(RequirementResult)
        .where(RequirementResult.attempt_id == attempt.id)
        .order_by(RequirementResult.evaluated_at, RequirementResult.id)
    )).all())
    current_results = {
        item.requirement_id: item
        for item in results
        if latest_snapshot is not None and item.snapshot_id == latest_snapshot.id
    }
    aggregate = await service.evaluator.evaluate(session, attempt_id=attempt.id)
    events = list((await session.scalars(
        select(TeachingEvent).where(TeachingEvent.attempt_id == attempt.id).order_by(TeachingEvent.created_at)
    )).all())
    interventions = list((await session.scalars(
        select(Intervention).where(Intervention.attempt_id == attempt.id).order_by(Intervention.created_at.desc())
    )).all())
    teacher_feedback_messages = [
        {"message": event.payload.get("message"), "time": event.created_at.isoformat(), "source": "teacher"}
        for event in events
        if event.event_type == "teacher_feedback"
        and (event.payload or {}).get("stage") == current_stage.stage_key
    ]
    # A resolved intervention also has a response, but the default response is
    # a system status update. Prefer an authored teacher message when one exists
    # so a later system transition cannot hide the actual guidance.
    system_feedback_messages = [
        {"message": item.response, "time": item.resolved_at.isoformat(), "source": "system"}
        for item in interventions if item.status == "RESOLVED" and item.response and item.resolved_at
    ]
    teacher_feedback = max(teacher_feedback_messages, key=lambda item: item["time"], default=None)
    if teacher_feedback is None:
        teacher_feedback = max(system_feedback_messages, key=lambda item: item["time"], default=None)
    source_root = manager.source_directory(attempt.id)
    files = []
    for path in sorted(item for item in source_root.rglob("*") if item.is_file() and not item.is_symlink()):
        if path.suffix.lower() not in ALLOWED_FILE_SUFFIXES:
            continue
        relative = path.relative_to(source_root).as_posix()
        size = path.stat().st_size
        files.append({"path": relative, "name": path.name, "size": size})
    guidance_events = [
        event
        for event in events
        if isinstance((event.payload or {}).get("guidance"), dict)
        and (event.payload or {}).get("guidance", {}).get("success") is True
        and (event.payload or {}).get("stage") == current_stage.stage_key
        and latest_snapshot is not None
        and (event.payload or {}).get("snapshot_id") == latest_snapshot.id
    ]
    latest_guidance = (guidance_events[-1].payload or {}).get("guidance") if guidance_events else None
    timeline = [
        {
            "time": event.created_at.isoformat(),
            "label": _event_label(event),
            "state_version": event.state_version,
            "payload": _safe_payload(event.payload or {}),
        }
        for event in events
    ]
    trace: list[dict[str, Any]] = []
    evaluator_by_operation = {item.operation_id: item.evaluator for item in results}
    for event in events:
        payload = event.payload or {}
        decision = payload.get("decision")
        if decision == "execute_tool":
            tool_operation_id = f"tool:{event.operation_id.removeprefix('persist:')}"
            evaluator = evaluator_by_operation.get(tool_operation_id, "")
            executor_label = (
                "Python Runner 运行证据" if evaluator.startswith("docker_runner:") else
                "OpenHands 实训证据" if evaluator.startswith("openhands:") else
                "隔离运行证据"
            )
            trace.extend([
                {"kind": "student", "label": "学生行为", "detail": "提交当前 Snapshot 并运行验收"},
                {"kind": "executor", "label": executor_label, "detail": _event_label(event)},
                {"kind": "evaluator", "label": "RequirementEvaluator", "detail": "按 Snapshot 聚合版本化验收结果"},
                {"kind": "langgraph", "label": "LangGraph 教学决策", "detail": f"服务端路由：{decision}"},
            ])
        elif decision == "generate_guidance":
            guidance = payload.get("guidance") or {}
            trace.append({
                "kind": "deepseek", "label": "DeepSeek 教学表达",
                "detail": guidance.get("message") if guidance.get("success") is True else "模型未生成可用建议",
            })
    stage_objectives = (version.policy or {}).get("stage_objectives", {})
    requirement_names = (version.policy or {}).get("requirement_names", {})
    if not isinstance(stage_objectives, dict):
        stage_objectives = {}
    if not isinstance(requirement_names, dict):
        requirement_names = {}
    entry_stage = str((version.policy or {}).get("start_stage") or stages[0].stage_key)
    entry_position = next(
        (item.position for item in stages if item.stage_key == entry_stage), 0
    )
    return {
        "identity": {"user_id": learner.id, "display_name": learner.display_name},
        "course": {"code": course.code, "name": course.name},
        "task": {"key": task.task_key, "title": task.title, "version": version.version},
        "attempt": {
            "id": attempt.id, "mode": attempt.mode, "status": attempt.status,
            "state_version": attempt.state_version,
            "student_failure_count": attempt.student_failure_count,
            "infrastructure_failure_count": attempt.infrastructure_failure_count,
            "ai_guidance_paused": attempt.ai_guidance_paused,
        },
        "stage": {
            "id": current_stage.id, "key": current_stage.stage_key, "title": current_stage.title,
            "position": current_stage.position, "total": len(stages),
            "objective": STAGE_OBJECTIVES.get(current_stage.stage_key)
            or stage_objectives.get(current_stage.stage_key, "完成本阶段验收项。"),
        },
        "stages": [
            {"key": item.stage_key, "title": item.title, "position": item.position,
             "status": (
                 "skipped" if item.position < entry_position else
                 "complete" if item.position < current_stage.position else
                 "current" if item.id == current_stage.id else "upcoming"
             )}
            for item in stages
        ],
        "requirements": [
            {
                "id": definition.id,
                "key": definition.requirement_key,
                "name": REQUIREMENT_NAMES.get(definition.requirement_key)
                or (definition.config or {}).get("display_name")
                or requirement_names.get(definition.requirement_key)
                or definition.requirement_key,
                "student_goal": REQUIREMENT_STUDENT_GUIDANCE.get(
                    definition.requirement_key, {}
                ).get("goal", ""),
                "success_criteria": REQUIREMENT_STUDENT_GUIDANCE.get(
                    definition.requirement_key, {}
                ).get("success", ""),
                "kind": definition.kind,
                "required": definition.required,
                "status": current_results.get(definition.id).status if definition.id in current_results else "NOT_RUN",
                "snapshot_id": current_results.get(definition.id).snapshot_id if definition.id in current_results else None,
                "snapshot_label": _snapshot_label(latest_snapshot) if definition.id in current_results else None,
                "operation_id": current_results.get(definition.id).operation_id if definition.id in current_results else None,
                "evaluator": current_results.get(definition.id).evaluator if definition.id in current_results else definition.evaluator,
                "min_length": int((definition.config or {}).get("min_length", 0)) if definition.kind == "STUDENT_EXPLANATION" else None,
                "evidence_refs": current_results.get(definition.id).evidence_refs if definition.id in current_results else [],
                "evaluated_at": current_results.get(definition.id).evaluated_at.isoformat() if definition.id in current_results else None,
                "has_old_result": any(
                    result.requirement_id == definition.id
                    and (latest_snapshot is None or result.snapshot_id != latest_snapshot.id)
                    for result in results
                ),
            }
            for definition in definitions
        ],
        "requirement_summary": {
            "satisfied": aggregate.satisfied,
            "satisfied_count": aggregate.satisfied_count,
            "required_count": aggregate.required_count,
        },
        "snapshots": [
            {"id": item.id, "sequence": item.sequence, "label": _snapshot_label(item), "created_at": item.created_at.isoformat()}
            for item in snapshots
        ],
        "latest_snapshot": (
            {"id": latest_snapshot.id, "sequence": latest_snapshot.sequence,
             "label": _snapshot_label(latest_snapshot), "created_at": latest_snapshot.created_at.isoformat()}
            if latest_snapshot else None
        ),
        "files": files,
        "teacher_feedback": teacher_feedback,
        "help_requested": next((event.event_type == "help_requested" for event in reversed(events)
                                if event.event_type in {"help_requested", "teacher_feedback"}), False),
        "guidance": latest_guidance,
        "guidance_generated_at": guidance_events[-1].created_at.isoformat() if guidance_events else None,
        "guidance_snapshot": (
            {"id": latest_snapshot.id, "label": _snapshot_label(latest_snapshot)}
            if latest_guidance and latest_snapshot else None
        ),
        "guidance_history": [
            {"time": event.created_at.isoformat(), **_safe_payload(event.payload or {}).get("guidance", {})}
            for event in guidance_events
        ],
        "student_observation": next((
            str((event.payload or {}).get("student_observation"))
            for event in reversed(events) if (event.payload or {}).get("student_observation")
            and (event.payload or {}).get("stage") == current_stage.stage_key
        ), ""),
        "intervention": (
            {"id": interventions[0].id, "status": interventions[0].status,
             "reason": interventions[0].reason, "created_at": interventions[0].created_at.isoformat()}
            if interventions else None
        ),
        "timeline": timeline,
        "agent_trace": trace[-12:],
        "self_service_tools": [
            {"name": name, "label": SELF_SERVICE_TOOL_LABELS.get(name, name)}
            for name in (PYB01_POLICY["self_service_tools"] if pack is not None else SELF_SERVICE_TOOLS)
        ],
        "latest_submission": await _submission_summary(session, attempt_id=attempt.id),
    }


async def _submission_summary(session: AsyncSession, *, attempt_id: str) -> dict[str, Any] | None:
    submission = await reviews.latest_submission(session, attempt_id=attempt_id)
    if submission is None:
        return None
    snapshot = await session.get(Snapshot, submission.snapshot_id)
    review = await session.scalar(select(Review).where(Review.submission_id == submission.id))
    grade = await session.scalar(select(FormalGrade).where(FormalGrade.submission_id == submission.id))
    return {
        "id": submission.id,
        "sequence": submission.sequence,
        "snapshot_id": submission.snapshot_id,
        "snapshot_label": _snapshot_label(snapshot),
        "created_at": submission.created_at.isoformat(),
        "review_status": review.status if review else "draft",
        "formal_grade": None if grade is None else {
            "total_score": grade.total_score,
            "max_score": grade.max_score,
            "published_at": grade.published_at.isoformat(),
        },
    }


@router.get("/student/assignments")
async def student_assignments(
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    if user.system_role != "student":
        raise HTTPException(403, "student_role_required")
    rows = list((await session.execute(
        select(Attempt, Task, TaskVersion, Course)
        .join(TaskVersion, Attempt.task_version_id == TaskVersion.id)
        .join(Task, TaskVersion.task_id == Task.id)
        .join(Course, Task.course_id == Course.id)
        .join(CourseMembership, CourseMembership.course_id == Course.id)
        .where(Attempt.learner_id == user.id,
               CourseMembership.user_id == user.id,
               CourseMembership.role == "student")
        .order_by(Course.name, Task.task_key, Attempt.created_at.desc())
    )).all())
    return {"assignments": [
        {"attempt_id": attempt.id, "task_key": task.task_key, "task_title": task.title,
         "task_version": version.version, "course_name": course.name,
         "status": attempt.status}
        for attempt, task, version, course in rows
    ]}


@router.get("/attempts/{attempt_id}/workbench")
async def workbench(
    attempt_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    return await _workbench_view(session, attempt=attempt, manager=_manager(request))


@router.get("/attempts/{attempt_id}/files/{file_path:path}")
async def read_file(
    attempt_id: str,
    file_path: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    await _attempt(session, attempt_id=attempt_id, user=user)
    root = _manager(request).source_directory(attempt_id)
    path = _relative_file(root, file_path)
    if not path.is_file():
        raise HTTPException(404, "file_not_found")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(413, "file_too_large")
    content = path.read_text(encoding="utf-8")
    return {"path": file_path, "content": content, "hash": _file_hash(content), "size": path.stat().st_size}


@router.put("/attempts/{attempt_id}/files/{file_path:path}")
async def save_file(
    attempt_id: str,
    file_path: str,
    body: FileSave,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_workspace_write_required")
    root = _manager(request).source_directory(attempt_id)
    path = _relative_file(root, file_path)
    created = not path.exists()
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if _file_hash(current) != body.expected_hash:
        raise HTTPException(409, detail={"code": "snapshot_conflict", "message": "文件已在其他位置更新，请重新载入后再保存"})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".saving")
    content = body.content.replace("\r\n", "\n").replace("\r", "\n")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return {
        "path": file_path,
        "hash": _file_hash(content),
        "saved_at": datetime.now(UTC).isoformat(),
        "created": created,
    }


@router.patch("/attempts/{attempt_id}/files/{file_path:path}")
async def move_file(
    attempt_id: str,
    file_path: str,
    body: FileMove,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_workspace_write_required")
    root = _manager(request).source_directory(attempt_id)
    source = _relative_file(root, file_path)
    target = _relative_file(root, body.target_path)
    if not source.is_file():
        raise HTTPException(404, "file_not_found")
    current = source.read_text(encoding="utf-8")
    if _file_hash(current) != body.expected_hash:
        raise HTTPException(409, detail={"code": "file_conflict", "message": "文件已更新，请重新载入后再重命名"})
    if target.exists():
        raise HTTPException(409, detail={"code": "file_exists", "message": "目标文件已存在，请使用其他名称"})
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)
    parent = source.parent
    while parent != root and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    return {"path": body.target_path, "hash": _file_hash(current), "size": len(current.encode("utf-8"))}


@router.delete("/attempts/{attempt_id}/files/{file_path:path}")
async def delete_file(
    attempt_id: str,
    file_path: str,
    body: FileDelete,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_workspace_write_required")
    root = _manager(request).source_directory(attempt_id)
    path = _relative_file(root, file_path)
    if not path.is_file():
        raise HTTPException(404, "file_not_found")
    current = path.read_text(encoding="utf-8")
    if _file_hash(current) != body.expected_hash:
        raise HTTPException(409, detail={"code": "file_conflict", "message": "文件已更新，请重新载入后再删除"})
    path.unlink()
    parent = path.parent
    while parent != root and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    return {"deleted": True, "path": file_path}


@router.post("/attempts/{attempt_id}/snapshots")
async def create_snapshot(
    attempt_id: str,
    body: SnapshotCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_snapshot_required")
    scope = f"snapshot:{attempt.id}"
    existing = await session.scalar(select(OperationLedger).where(
        OperationLedger.scope == scope, OperationLedger.operation_id == body.operation_id
    ))
    if existing is not None:
        item = await session.get(Snapshot, str(existing.result_ref or "").removeprefix("snapshot:"))
        if item is None:
            raise HTTPException(500, "snapshot_ledger_corrupt")
        return {"id": item.id, "sequence": item.sequence, "label": _snapshot_label(item), "duplicate": True}
    manager = _manager(request)
    if body.expected_file_hash is not None:
        checked_path = _relative_file(manager.source_directory(attempt.id), body.expected_file_path)
        current = checked_path.read_text(encoding="utf-8") if checked_path.exists() else ""
        if _file_hash(current) != body.expected_file_hash:
            raise HTTPException(409, detail={"code": "snapshot_conflict", "message": "代码在保存后又发生了变化。请重新保存，再创建检查版本。"})
    if body.reuse_unchanged:
        latest = await _latest_snapshot(session, attempt.id)
        if latest and latest.snapshot_ref.rsplit("#", 1)[-1] == _tree_digest(manager.source_directory(attempt.id)):
            session.add(OperationLedger(scope=scope, operation_id=body.operation_id, status="COMPLETED",
                result_ref=f"snapshot:{latest.id}", result_payload={"sequence": latest.sequence}))
            await session.commit()
            return {"id": latest.id, "sequence": latest.sequence, "label": _snapshot_label(latest), "duplicate": True}
    sequence = int(await session.scalar(select(func.coalesce(func.max(Snapshot.sequence), 0)).where(Snapshot.attempt_id == attempt.id))) + 1
    snapshot_id = str(uuid4())
    _, snapshot_ref = manager.create_snapshot(attempt_id=attempt.id, snapshot_id=snapshot_id)
    item = await service.register_snapshot(
        session, attempt=attempt, snapshot_id=snapshot_id, snapshot_ref=snapshot_ref, sequence=sequence
    )
    session.add(OperationLedger(
        scope=scope, operation_id=body.operation_id, status="COMPLETED",
        result_ref=f"snapshot:{item.id}", result_payload={"sequence": item.sequence},
    ))
    await session.commit()
    return {"id": item.id, "sequence": item.sequence, "label": _snapshot_label(item), "duplicate": False}


async def _latest_snapshot(session: AsyncSession, attempt_id: str) -> Snapshot | None:
    return await session.scalar(
        select(Snapshot).where(Snapshot.attempt_id == attempt_id).order_by(Snapshot.sequence.desc()).limit(1)
    )


def _graph_event(
    *,
    attempt: Attempt,
    user: User,
    body: RunCreate | GuidanceCreate | ProgramRunCreate,
    event_type: str,
    task_version: str,
    requested_tool: str = "run_faq_tests",
    evidence_refs: list[str] | None = None,
    evidence_summary: str = "",
) -> GraphEvent:
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "attempt_id": attempt.id,
        "task_version": task_version,
        "actor_id": user.id,
        "actor_role": "student",
        "expected_state_version": body.expected_state_version,
        "operation_id": body.operation_id,
        "snapshot_id": body.snapshot_id,
        "evidence_refs": list(evidence_refs or []),
        "evidence_summary": evidence_summary,
        "student_observation": str(getattr(body, "observation", "") or "").strip(),
        "failure_origin": "none",
        "requested_guidance_kind": "question",
        "requested_tool": requested_tool if event_type == "run_tool" else "",
        "stdin_text": body.stdin_text if isinstance(body, ProgramRunCreate) else None,
    }


def _python_error_location(text: str) -> dict[str, Any] | None:
    matches = re.findall(r'File "/workspace/student/([^"]+)", line (\d+)', text)
    if not matches:
        return None
    file_name, line_number = matches[-1]
    return {"file": file_name, "line": int(line_number)}


def _console_view(
    *,
    attempt: Attempt,
    operation_id: str,
    requested_tool: str,
    snapshot: Snapshot | None,
    state: dict[str, Any],
    manager: AttemptWorkspaceManager,
) -> dict[str, Any]:
    """Bounded raw output of one advisory run, read from its recorded artifact."""
    digest = hashlib.sha256(f"tool:{operation_id}".encode()).hexdigest()
    artifact_path = manager.evidence_directory(attempt.id) / f"{digest}.artifact.json"
    artifact: dict[str, Any] = {}
    if artifact_path.is_file():
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            artifact = {}
    request_data = artifact.get("request") if isinstance(artifact.get("request"), dict) else {}
    details = artifact.get("details") if isinstance(artifact.get("details"), dict) else {}
    raw_stdout = str(artifact.get("stdout") or "")
    raw_stderr = str(artifact.get("stderr") or "")
    tool = str(request_data.get("tool_name") or requested_tool)
    pack = task_pack_for(str(request_data.get("task_version") or ""))
    sample = pack.public_cases[0] if pack and tool in {"run_python_sample", "run_python_trace"} else None
    manual_stdin = request_data.get("stdin_text")
    run_stdin = manual_stdin if isinstance(manual_stdin, str) else sample[1] if sample else None
    input_label = sample[0] if sample and run_stdin == sample[1] else "空输入" if run_stdin == "" else "自定义输入"
    structured = details.get("structured") if tool == "run_python_trace" and isinstance(details.get("structured"), dict) else {}
    if structured:
        raw_stdout = str(structured.get("stdout") or "")
        raw_stderr = str(structured.get("stderr") or "")
    trace = structured.get("trace") if isinstance(structured.get("trace"), list) else []
    return {
        "tool": tool,
        "label": f"{'逐行调试' if tool == 'run_python_trace' else '试运行'} · {input_label}" if sample else SELF_SERVICE_TOOL_LABELS.get(tool, tool),
        "status": str(artifact.get("status") or state.get("last_tool_status") or "unknown"),
        "code": str(artifact.get("code") or "not_recorded"),
        "exit_code": structured.get("exit_code") if isinstance(structured.get("exit_code"), int) else details.get("exit_code") if isinstance(details.get("exit_code"), int) else None,
        "stdout": raw_stdout[:CONSOLE_STDOUT_CHARS],
        "stdout_truncated": bool(structured.get("stdout_truncated")) or bool(artifact.get("stdout_truncated")) or len(raw_stdout) > CONSOLE_STDOUT_CHARS,
        "stderr": raw_stderr[:CONSOLE_STDERR_CHARS],
        "stderr_truncated": bool(structured.get("stderr_truncated")) or bool(artifact.get("stderr_truncated")) or len(raw_stderr) > CONSOLE_STDERR_CHARS,
        "location": _python_error_location(raw_stderr or raw_stdout),
        "sample_input": run_stdin,
        "input_source": "sample" if sample and run_stdin == sample[1] else "custom" if manual_stdin is not None else None,
        "trace": trace[:72],
        "trace_truncated": bool(structured.get("trace_truncated")),
        "snapshot": _snapshot_label(snapshot),
        "snapshot_id": snapshot.id if snapshot is not None else None,
        "operation": f"tool:{operation_id}"[:72],
        "recorded": artifact_path.is_file(),
        "state_version": state.get("state_version"),
    }


def _public_check_diagnosis(
    manager: AttemptWorkspaceManager, *, attempt_id: str, snapshot_id: str, operation_id: str
) -> str:
    """Summarize only current-snapshot public diagnostics for a guidance request."""
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    path = manager.evidence_directory(attempt_id) / f"{digest}.artifact.json"
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 256_000:
            return ""
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(artifact, dict):
        return ""
    request_data = artifact.get("request")
    if not isinstance(request_data, dict) or (
        request_data.get("attempt_id") != attempt_id
        or request_data.get("snapshot_id") != snapshot_id
        or request_data.get("tool_name") != "run_python_public_tests"
    ):
        return ""
    details = artifact.get("details")
    structured = details.get("structured") if isinstance(details, dict) else None
    if not isinstance(structured, dict):
        return ""
    checks = structured.get("checks")
    diagnoses: list[str] = []
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, dict) or item.get("passed"):
                continue
            code = item.get("diagnosis_code")
            if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) and code not in diagnoses:
                diagnoses.append(code)
    facts = ["observed_public_diagnosis=" + ",".join(diagnoses[:3])] if diagnoses else []
    error = str(structured.get("error") or "")[-2400:]
    exception_types = list(re.finditer(r"(?m)^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):", error))
    if exception_types:
        facts.append("observed_public_error=" + exception_types[-1].group(1))
    location = _python_error_location(error)
    if location and location["file"] == "main.py":
        facts.append(f"observed_public_location=main.py:{location['line']}")
    return " ".join(facts)


async def _snapshot_requirement_evidence(
    session: AsyncSession, *, manager: AttemptWorkspaceManager, attempt_id: str, snapshot_id: str
) -> tuple[list[str], str]:
    rows = list((await session.execute(
        select(RequirementResult, RequirementDefinition.requirement_key)
        .join(
            RequirementDefinition,
            RequirementDefinition.id == RequirementResult.requirement_id,
        )
        .where(
            RequirementResult.attempt_id == attempt_id,
            RequirementResult.snapshot_id == snapshot_id,
        )
        .order_by(RequirementResult.evaluated_at, RequirementResult.id)
    )).all())
    refs: list[str] = []
    facts: list[str] = []
    for result, requirement_key in rows:
        refs.extend(ref for ref in result.evidence_refs if ref not in refs)
        facts.append(f"{requirement_key}={result.status}")
        if result.status == "NOT_SATISFIED" and requirement_key in {
            "addition_public_tests", "passing_public_tests", "traversal_public_tests",
        }:
            diagnosis = _public_check_diagnosis(
                manager, attempt_id=attempt_id, snapshot_id=snapshot_id,
                operation_id=result.operation_id,
            )
            if diagnosis:
                facts.append(diagnosis)
    return refs, "；".join(facts)


@router.post("/attempts/{attempt_id}/runs")
async def run_checks(
    attempt_id: str,
    body: RunCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_run_required")
    latest = await _latest_snapshot(session, attempt.id)
    if latest is None or latest.id != body.snapshot_id:
        raise HTTPException(409, detail={"code": "old_snapshot", "message": "当前代码已有更新。请创建新的检查版本后再运行。"})
    if attempt.state_version != body.expected_state_version:
        raise HTTPException(409, detail={"code": "stale_state_version", "message": "教学状态已更新，请刷新后重试"})
    version = await session.get(TaskVersion, attempt.task_version_id)
    task = await session.get(Task, version.task_id) if version else None
    if version is None or task is None:
        raise HTTPException(500, "attempt_context_incomplete")
    current_stage = await session.get(TaskStage, attempt.current_stage_id)
    check_tools = STAGE_CHECK_TOOLS.get(
        current_stage.stage_key if current_stage else "", ("run_faq_tests",)
    )
    states: list[dict[str, Any]] = []
    statuses: list[str] = []
    expected_state_version = body.expected_state_version
    try:
        for index, tool_name in enumerate(check_tools):
            operation_id = (
                body.operation_id
                if len(check_tools) == 1
                else f"{body.operation_id[:124]}:{index}:{tool_name}"
            )
            part = body.model_copy(
                update={
                    "operation_id": operation_id,
                    "expected_state_version": expected_state_version,
                }
            )
            state = await _runtime(request).run_event(
                attempt_id=attempt.id,
                event=_graph_event(
                    attempt=attempt,
                    user=user,
                    body=part,
                    event_type="run_tool",
                    task_version=f"{task.task_key}-{version.version}",
                    requested_tool=tool_name,
                ),
                automatic_followup=index == len(check_tools) - 1,
            )
            states.append(cast(dict[str, Any], state))
            error_code = str(state.get("error_code") or "")
            decision = cast(dict[str, Any], state.get("decision") or {})
            if decision.get("kind") == "reject" or error_code:
                raise HTTPException(
                    503,
                    detail={
                        "code": "check_policy_unavailable",
                        "message": "本阶段检查配置暂不可用，代码和学习记录均已保留。",
                        "reason": error_code or "CHECK_REJECTED",
                    },
                )
            tool_status = state.get("last_tool_status")
            if not tool_status:
                raise HTTPException(
                    503,
                    detail={
                        "code": "check_result_missing",
                        "message": "检查未返回有效结果，代码和学习记录均已保留。",
                    },
                )
            statuses.append(str(tool_status))
            expected_state_version = int(state.get("state_version", expected_state_version))
            if statuses[-1] == "infrastructure_failure" or state.get("current_stage") not in {None, current_stage.stage_key}:
                break
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "runtime_unavailable":
            raise HTTPException(503, detail={"code": "openhands_unavailable", "message": "实训执行服务暂不可用"}) from exc
        raise
    except Exception as exc:
        logger.exception("Practice check failed before an execution result was returned")
        raise HTTPException(503, detail={"code": "openhands_unavailable", "message": "实训执行服务暂不可用"}) from exc
    state = states[-1]
    aggregate_status = (
        "infrastructure_failure"
        if "infrastructure_failure" in statuses
        else "student_failure"
        if "student_failure" in statuses
        else "succeeded"
    )
    return {
        "flow_status": state.get("flow_status"),
        "last_tool_status": aggregate_status,
        "help_level": state.get("help_level"),
        "guidance": state.get("guidance"),
        "stage_assessment": state.get("stage_assessment"),
        "error_code": state.get("error_code"),
        "state_version": state.get("state_version"),
        "snapshot_id": state.get("latest_snapshot_id"),
    }


@router.post("/attempts/{attempt_id}/program-runs")
async def run_program(
    attempt_id: str,
    body: ProgramRunCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    """Run the learner's own program and return its raw output.

    Advisory only: it records evidence and a teaching event, but it never writes a
    requirement result, never counts as a learning failure and never triggers the
    automatic model follow-up that a failed acceptance check does.
    """
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_run_required")
    version = await session.get(TaskVersion, attempt.task_version_id)
    task = await session.get(Task, version.task_id) if version else None
    pack = task_pack_for(f"{task.task_key}-{version.version}") if task and version else None
    allowed_tools = PYB01_POLICY["self_service_tools"] if pack is not None else SELF_SERVICE_TOOLS
    if body.tool not in allowed_tools:
        raise HTTPException(
            422,
            detail={"code": "tool_not_allowed", "message": "该运行方式不在本任务允许的范围内。"},
        )
    if body.stdin_text is not None and (
        body.tool not in {"run_python_sample", "run_python_trace"}
        or len(body.stdin_text.encode("utf-8")) > 4096
    ):
        raise HTTPException(
            422,
            detail={"code": "invalid_stdin", "message": "手动输入仅可用于 Python 试运行或调试，且不能超过 4 KB。"},
        )
    latest = await _latest_snapshot(session, attempt.id)
    if latest is None or latest.id != body.snapshot_id:
        raise HTTPException(409, detail={"code": "old_snapshot", "message": "当前代码已有更新。请创建新的检查版本后再运行。"})
    if attempt.state_version != body.expected_state_version:
        raise HTTPException(409, detail={"code": "stale_state_version", "message": "教学状态已更新，请刷新后重试"})
    if version is None or task is None:
        raise HTTPException(500, "attempt_context_incomplete")
    try:
        state = await _runtime(request).run_event(
            attempt_id=attempt.id,
            event=_graph_event(
                attempt=attempt,
                user=user,
                body=body,
                event_type="run_tool",
                task_version=f"{task.task_key}-{version.version}",
                requested_tool=body.tool,
            ),
            automatic_followup=False,
        )
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    except Exception as exc:
        raise HTTPException(503, detail={"code": "openhands_unavailable", "message": "实训执行服务暂不可用"}) from exc
    return _console_view(
        attempt=attempt,
        operation_id=body.operation_id,
        requested_tool=body.tool,
        snapshot=latest,
        state=cast(dict[str, Any], state),
        manager=_manager(request),
    )


def _terminal_manager(request: Request) -> InteractivePythonManager:
    manager = getattr(request.app.state, "interactive_python", None)
    if manager is None:
        raise HTTPException(503, detail={"code": "runner_unavailable", "message": "Python 终端暂不可用。"})
    return cast(InteractivePythonManager, manager)


@router.post("/attempts/{attempt_id}/terminal-sessions")
async def start_terminal_session(
    attempt_id: str, body: TerminalStartCreate, request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_run_required")
    version = await session.get(TaskVersion, attempt.task_version_id)
    task = await session.get(Task, version.task_id) if version else None
    pack = task_pack_for(f"{task.task_key}-{version.version}") if task and version else None
    if pack is None or "run_python_sample" not in PYB01_POLICY["self_service_tools"]:
        raise HTTPException(422, detail={"code": "terminal_not_allowed", "message": "当前任务不支持交互式 Python 终端。"})
    latest = await _latest_snapshot(session, attempt.id)
    if latest is None or latest.id != body.snapshot_id:
        raise HTTPException(409, detail={"code": "old_snapshot", "message": "当前代码已有更新，请重新运行。"})
    if attempt.state_version != body.expected_state_version:
        raise HTTPException(409, detail={"code": "stale_state_version", "message": "教学状态已更新，请刷新后重试。"})
    try:
        return await asyncio.to_thread(
            _terminal_manager(request).start,
            operation_id=body.operation_id, attempt_id=attempt.id, owner_id=user.id,
            snapshot_id=latest.id, snapshot_label=_snapshot_label(latest),
        )
    except TerminalAlreadyRunning as exc:
        raise HTTPException(409, detail={"code": "terminal_already_running", "message": "当前已有程序在运行，请先结束它。"}) from exc
    except (OSError, RuntimeError) as exc:
        logger.exception("Failed to start interactive Python terminal")
        raise HTTPException(503, detail={"code": "runner_unavailable", "message": "Python 终端启动失败，请稍后重试。"}) from exc


@router.get("/attempts/{attempt_id}/terminal-sessions/active")
async def active_terminal_session(
    attempt_id: str, request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_run_required")
    return {"session": _terminal_manager(request).active(attempt_id=attempt.id, owner_id=user.id)}


async def _owned_terminal(
    attempt_id: str, terminal_id: str, request: Request, session: AsyncSession, user: User,
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_run_required")
    terminal = _terminal_manager(request).get(terminal_id, attempt_id=attempt.id, owner_id=user.id)
    if terminal is None:
        raise HTTPException(404, detail={"code": "terminal_not_found", "message": "终端会话不存在或已过期。"})
    return terminal


@router.get("/attempts/{attempt_id}/terminal-sessions/{terminal_id}")
async def terminal_session(
    attempt_id: str, terminal_id: str, request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    terminal = await _owned_terminal(attempt_id, terminal_id, request, session, user)
    return terminal.snapshot()


@router.post("/attempts/{attempt_id}/terminal-sessions/{terminal_id}/input")
async def terminal_input(
    attempt_id: str, terminal_id: str, body: TerminalInputCreate, request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    terminal = await _owned_terminal(attempt_id, terminal_id, request, session, user)
    try:
        return await asyncio.to_thread(terminal.send_line, body.line)
    except TerminalNotRunning as exc:
        raise HTTPException(409, detail={"code": "terminal_not_running", "message": "程序已结束，请重新试运行。"}) from exc
    except TerminalInputLimit as exc:
        raise HTTPException(422, detail={"code": "terminal_input_limit", "message": "单行最多 1 KB，整次运行最多输入 4 KB。"}) from exc


@router.post("/attempts/{attempt_id}/terminal-sessions/{terminal_id}/stop")
async def stop_terminal_session(
    attempt_id: str, terminal_id: str, request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    terminal = await _owned_terminal(attempt_id, terminal_id, request, session, user)
    return await asyncio.to_thread(terminal.stop)


@router.post("/attempts/{attempt_id}/terminal-sessions/{terminal_id}/eof")
async def close_terminal_input(
    attempt_id: str, terminal_id: str, request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    terminal = await _owned_terminal(attempt_id, terminal_id, request, session, user)
    try:
        return await asyncio.to_thread(terminal.close_input)
    except TerminalNotRunning as exc:
        raise HTTPException(409, detail={"code": "terminal_not_running", "message": "输入已结束或程序已停止。"}) from exc


@router.post("/attempts/{attempt_id}/guidance")
async def request_guidance(
    attempt_id: str,
    body: GuidanceCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_guidance_required")
    if attempt.ai_guidance_paused:
        raise HTTPException(409, detail={"code": "ai_guidance_paused", "message": "教师已暂停智能指导"})
    latest = await _latest_snapshot(session, attempt.id)
    if latest is None or latest.id != body.snapshot_id:
        raise HTTPException(409, detail={"code": "old_snapshot", "message": "请先为当前代码创建检查版本。"})
    version = await session.get(TaskVersion, attempt.task_version_id)
    task = await session.get(Task, version.task_id) if version else None
    if version is None or task is None:
        raise HTTPException(500, "attempt_context_incomplete")
    evidence_refs, evidence_summary = await _snapshot_requirement_evidence(
        session, manager=_manager(request), attempt_id=attempt.id, snapshot_id=body.snapshot_id
    )
    try:
        state = await _runtime(request).run_event(
            attempt_id=attempt.id,
            event=_graph_event(
                attempt=attempt,
                user=user,
                body=body,
                event_type="request_guidance",
                task_version=f"{task.task_key}-{version.version}",
                evidence_refs=evidence_refs,
                evidence_summary=evidence_summary,
            ),
            automatic_followup=False,
        )
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    except Exception as exc:
        raise HTTPException(503, detail={"code": "model_unavailable", "message": "智能指导暂不可用，你仍可以继续编辑和运行检查"}) from exc
    return {"guidance": state.get("guidance"), "error_code": state.get("error_code"), "state_version": state.get("state_version")}


@router.get("/attempts/{attempt_id}/evidence/{operation_id:path}")
async def evidence_detail(
    attempt_id: str,
    operation_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    result = await session.scalar(select(RequirementResult).where(
        RequirementResult.attempt_id == attempt.id,
        RequirementResult.operation_id == operation_id,
    ))
    if result is None:
        raise HTTPException(404, "evidence_not_found")
    definition = await session.get(RequirementDefinition, result.requirement_id)
    snapshot = await session.get(Snapshot, result.snapshot_id)
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    artifact_path = _manager(request).evidence_directory(attempt.id) / f"{digest}.artifact.json"
    artifact: dict[str, Any] = {}
    if artifact_path.is_file():
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            artifact = {}
    request_data = artifact.get("request") if isinstance(artifact.get("request"), dict) else {}
    details = artifact.get("details") if isinstance(artifact.get("details"), dict) else {}
    structured = details.get("structured") if isinstance(details.get("structured"), dict) else {}
    raw_checks = structured.get("checks") if isinstance(structured.get("checks"), list) else []
    checks = [
        {
            "code": str(item.get("code") or "check"),
            "passed": bool(item.get("passed")),
            "detail": str(item.get("detail") or "")[:240],
            "diagnosis_code": str(item.get("diagnosis_code") or "")[:64],
        }
        for item in raw_checks
        if isinstance(item, dict)
    ]
    raw_error = str(structured.get("error") or artifact.get("stderr") or "")[-2400:]
    error_summary = raw_error.replace("/workspace/student/", "")
    location = _python_error_location(raw_error)
    bounded_stdout = str(artifact.get("stdout") or "")[:2400]
    return {
        "snapshot": _snapshot_label(snapshot),
        "operation": operation_id.removeprefix("tool:")[:72],
        "tool": request_data.get("tool_name", "run_faq_tests"),
        "requirement": REQUIREMENT_NAMES.get(definition.requirement_key, definition.requirement_key) if definition else "验收项",
        "status": result.status,
        "reason_code": artifact.get("code", "evidence_recorded"),
        "observed_at": result.evaluated_at.isoformat(),
        "stdout_summary": bounded_stdout,
        "stdout_truncated": bool(artifact.get("stdout_truncated")) or len(str(artifact.get("stdout") or "")) > len(bounded_stdout),
        "checks": checks,
        "skill_evidence": [
            {"skill_id": str(item.get("skill_id") or "")[:64],
             "status": str(item.get("status") or "")[:32],
             "case_code": str(item.get("case_code") or "")[:32]}
            for item in structured.get("skill_evidence", [])
            if isinstance(item, dict)
        ][:8],
        "error_summary": error_summary,
        "location": location,
        "artifacts": [
            {"kind": "证据清单", "ref": ref, "available": artifact_path.is_file()}
            for ref in result.evidence_refs
        ],
    }


def _diff_summary(manager: AttemptWorkspaceManager, attempt_id: str, snapshots: list[Snapshot]) -> dict[str, Any]:
    if len(snapshots) < 2:
        return {"from": None, "to": _snapshot_label(snapshots[-1]) if snapshots else None, "files_changed": 0, "additions": 0, "deletions": 0}
    before, after = snapshots[-2], snapshots[-1]
    before_root = manager.snapshot_directory(attempt_id, before.id)
    after_root = manager.snapshot_directory(attempt_id, after.id)
    names = sorted({p.relative_to(before_root).as_posix() for p in before_root.rglob("*") if p.is_file()} | {p.relative_to(after_root).as_posix() for p in after_root.rglob("*") if p.is_file()})
    changed = additions = deletions = 0
    changed_files: list[str] = []
    for name in names:
        old = (before_root / name).read_text(encoding="utf-8", errors="replace").splitlines() if (before_root / name).is_file() else []
        new = (after_root / name).read_text(encoding="utf-8", errors="replace").splitlines() if (after_root / name).is_file() else []
        if old == new:
            continue
        changed += 1
        changed_files.append(name)
        for line in difflib.ndiff(old, new):
            additions += line.startswith("+ ")
            deletions += line.startswith("- ")
    return {"from": _snapshot_label(before), "to": _snapshot_label(after), "files_changed": changed, "files": changed_files[:8], "additions": additions, "deletions": deletions}


@router.get("/teacher/classroom")
async def teacher_classroom(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    if user.system_role not in {"teacher", "admin"}:
        raise HTTPException(403, "teacher_role_required")
    course_ids = list((await session.scalars(select(CourseMembership.course_id).where(
        CourseMembership.user_id == user.id, CourseMembership.role.in_(["teacher", "owner"])
    ))).all())
    courses = list((await session.scalars(select(Course).where(Course.id.in_(course_ids)).order_by(Course.name))).all()) if course_ids else []
    course_by_id = {course.id: course for course in courses}
    attempts = list((await session.scalars(
        select(Attempt)
        .join(TaskVersion, Attempt.task_version_id == TaskVersion.id)
        .join(Task, TaskVersion.task_id == Task.id)
        .where(Task.course_id.in_(course_ids))
        .order_by(Attempt.created_at.desc())
    )).all()) if course_ids else []
    items = []
    now = datetime.now(UTC)
    for attempt in attempts:
        version = await session.get(TaskVersion, attempt.task_version_id)
        task = await session.get(Task, version.task_id) if version else None
        learner = await session.get(User, attempt.learner_id)
        stage = await session.get(TaskStage, attempt.current_stage_id)
        intervention = await session.scalar(select(Intervention).where(
            Intervention.attempt_id == attempt.id
        ).order_by(Intervention.created_at.desc()).limit(1))
        submission = await reviews.latest_submission(session, attempt_id=attempt.id)
        grade = await session.scalar(select(FormalGrade).where(FormalGrade.submission_id == submission.id)) if submission else None
        task_pack = task_pack_for(f"{task.task_key}-{version.version}") if task and version else None
        latest_snapshot = await _latest_snapshot(session, attempt.id) if task_pack else None
        public_check = await session.scalar(
            select(RequirementResult)
            .join(RequirementDefinition, RequirementResult.requirement_id == RequirementDefinition.id)
            .where(
                RequirementResult.attempt_id == attempt.id,
                RequirementResult.snapshot_id == latest_snapshot.id,
                RequirementDefinition.requirement_key == task_pack.public_requirement,
            )
            .order_by(RequirementResult.evaluated_at.desc(), RequirementResult.id.desc())
            .limit(1)
        ) if task_pack and latest_snapshot else None
        waiting = intervention and intervention.status in {"WAITING_TEACHER", "RESUME_FAILED"}
        latest_help = await session.scalar(select(TeachingEvent).where(
            TeachingEvent.attempt_id == attempt.id,
            TeachingEvent.event_type.in_(["help_requested", "teacher_feedback"]),
        ).order_by(TeachingEvent.created_at.desc()).limit(1))
        requested_help = latest_help is not None and latest_help.event_type == "help_requested"
        pending_review = submission is not None and grade is None
        if waiting or pending_review or requested_help:
            category = "attention"
        elif attempt.status == "completed" or grade is not None:
            category = "completed"
        else:
            category = "progress"
        reason = "正常学习中"
        if waiting:
            reason = intervention.reason
        elif requested_help:
            reason = "student_help_requested"
        elif pending_review:
            reason = "pending_review"
        elif grade is not None:
            reason = "grade_published"
        wait_from = intervention.created_at if waiting else latest_help.created_at if requested_help else (submission.created_at if submission else None)
        if wait_from is not None and wait_from.tzinfo is None:
            wait_from = wait_from.replace(tzinfo=UTC)
        wait_seconds = int((now - wait_from).total_seconds()) if category == "attention" and wait_from is not None else 0
        items.append({
            "attempt_id": attempt.id,
            "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
            "student_id": attempt.learner_id,
            "student": learner.display_name if learner else "未知学生",
            "course_id": task.course_id if task else None,
            "course_code": course_by_id[task.course_id].code if task and task.course_id in course_by_id else None,
            "task_key": task.task_key if task else None,
            "task_title": task.title if task else "未知任务",
            "public_check_status": public_check.status if public_check else "NOT_RUN",
            "stage": stage.title if stage else "未知阶段",
            "stage_key": stage.stage_key if stage else "",
            "reason": reason,
            "failure_count": attempt.student_failure_count,
            "help_level": next((
                str((event.payload or {}).get("help_level"))
                for event in reversed(list((await session.scalars(select(TeachingEvent).where(
                    TeachingEvent.attempt_id == attempt.id
                ).order_by(TeachingEvent.created_at))).all()))
                if (event.payload or {}).get("help_level") not in {None, "NONE"}
            ), "尚未指导"),
            "wait_seconds": wait_seconds,
            "intervention_id": intervention.id if intervention else None,
            "intervention_status": intervention.status if intervention else None,
            "category": category,
            "ai_guidance_paused": attempt.ai_guidance_paused,
            "submission_id": submission.id if submission else None,
            "review_status": "published" if grade else ("draft" if submission else None),
        })
    return {
        "teacher": {"id": user.id, "display_name": user.display_name},
        "courses": [{"id": course.id, "code": course.code, "name": course.name} for course in courses],
        "groups": {
            "attention": [item for item in items if item["category"] == "attention"],
            "progress": [item for item in items if item["category"] == "progress"],
            "completed": [item for item in items if item["category"] == "completed"],
        },
    }


@router.get("/teacher/attempts/{attempt_id}")
async def teacher_attempt_detail(
    attempt_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id == user.id:
        raise HTTPException(403, "teacher_role_required")
    view = await _workbench_view(session, attempt=attempt, manager=_manager(request))
    learner = await session.get(User, attempt.learner_id)
    snapshots = list((await session.scalars(select(Snapshot).where(
        Snapshot.attempt_id == attempt.id
    ).order_by(Snapshot.sequence))).all())
    interventions = list((await session.scalars(select(Intervention).where(
        Intervention.attempt_id == attempt.id
    ).order_by(Intervention.created_at.desc()))).all())
    view["identity"] = {"user_id": learner.id if learner else "", "display_name": learner.display_name if learner else "未知学生"}
    view["snapshot_diff"] = _diff_summary(_manager(request), attempt.id, snapshots)
    view["interventions"] = [
        {"id": item.id, "status": item.status, "reason": item.reason,
         "requested_state_version": item.requested_state_version,
         "response": item.response, "allow_l2": item.allow_l2,
         "created_at": item.created_at.isoformat(),
         "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None}
        for item in interventions
    ]
    return view


@router.post("/teacher/interventions/{intervention_id}/action")
async def teacher_action(
    intervention_id: str,
    body: TeacherAction,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    item = await session.get(Intervention, intervention_id)
    if item is None:
        raise HTTPException(404, "intervention_not_found")
    attempt = await _attempt(session, attempt_id=item.attempt_id, user=user)
    if attempt.learner_id == user.id:
        raise HTTPException(403, "teacher_role_required")
    if body.action not in {"continue", "allow_l2", "pause_ai", "resume"}:
        raise HTTPException(400, "invalid_teacher_action")
    pause = body.action == "pause_ai"
    attempt.ai_guidance_paused = pause
    await session.commit()
    response = body.teacher_prompt.strip() or {
        "continue": "继续当前帮助级别，请学生先完成下一次验证。",
        "allow_l2": "允许提供局部示例，但仍由学生自行修改代码。",
        "pause_ai": "暂停智能指导，等待教师继续处理。",
        "resume": "恢复教学流程。",
    }[body.action]
    resume_operation_id = body.operation_id
    if item.status == "RESUME_FAILED":
        retry_ledger = await session.scalar(
            select(OperationLedger)
            .where(OperationLedger.scope == f"intervention-resume:{item.id}")
            .order_by(OperationLedger.created_at.desc())
            .limit(1)
        )
        if retry_ledger is not None:
            resume_operation_id = retry_ledger.operation_id
    try:
        resolved, state = await _runtime(request).resume_intervention_with_state(
            intervention_id=intervention_id,
            teacher_id=user.id,
            resume_operation_id=resume_operation_id,
            expected_state_version=body.expected_state_version,
            response=response,
            allow_l2=body.action == "allow_l2",
        )
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    except Exception as exc:
        raise HTTPException(503, detail={"code": "resume_failed", "message": "恢复教学流程失败，教师操作已保留，可安全重试"}) from exc
    # Preserve an authored prompt separately from the intervention's generic
    # response so the student can distinguish teacher guidance from system state.
    authored_prompt = body.teacher_prompt.strip()
    if authored_prompt:
        await session.refresh(attempt)
        try:
            await _learning_message(
                session,
                attempt=attempt,
                user=user,
                body=LearningMessage(
                    operation_id=f"teacher-feedback:{body.operation_id}"[:160],
                    expected_state_version=attempt.state_version,
                    message=authored_prompt,
                ),
                event_type="teacher_feedback",
            )
        except (BusinessRuleError, HTTPException):
            # The intervention result is already committed; a concurrent state
            # change must not turn a successful teacher action into a failure.
            pass
    return {"status": resolved.status, "action": body.action, "flow_status": state.get("flow_status"), "guidance": state.get("guidance")}


@router.get("/attempts/{attempt_id}/stream")
async def event_stream(
    attempt_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
    since_state_version: int | None = None,
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if since_state_version is not None and since_state_version < 0:
        raise HTTPException(400, "invalid_stream_cursor")
    start_version = attempt.state_version if since_state_version is None else since_state_version
    factory = request.app.state.session_factory

    async def generate():
        cursor = start_version
        yield f"event: ready\ndata: {json.dumps({'state_version': cursor})}\n\n"
        idle = 0
        while not await request.is_disconnected():
            async with factory() as stream_session:
                events = list((await stream_session.scalars(select(TeachingEvent).where(
                    TeachingEvent.attempt_id == attempt_id,
                    TeachingEvent.state_version > cursor,
                ).order_by(TeachingEvent.state_version))).all())
            if events:
                idle = 0
                for event in events:
                    cursor = max(cursor, event.state_version)
                    payload = {"state_version": event.state_version, "label": _event_label(event), "created_at": event.created_at.isoformat()}
                    yield f"id: {event.state_version}\nevent: teaching_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            else:
                idle += 1
                if idle % 10 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _product_fail(error: BusinessRuleError) -> HTTPException:
    code = str(error)
    messages = {
        "student_submit_required": "只有该 Attempt 的学生可以提交作品",
        "snapshot_attempt_mismatch": "检查版本不属于当前任务",
        "stale_snapshot": "提交必须使用最新检查版本",
        "review_already_published": "评价已发布，不能再覆盖草稿",
        "unknown_rubric_item": "量规项不存在",
        "formal_grade_not_writable": "正式成绩不能由请求体或 AI 建议写入",
        "score_out_of_range": "分值超出该量规项上限",
        "unconfirmed_rubric_item": "仍有量规项待评价，不能发布正式成绩",
        "teacher_reason_required": "确认或改分必须填写理由",
        "rubric_incomplete": "量规项尚未准备完成",
        "teacher_review_required": "只有教师复核类验收项可以这样确认",
        "invalid_review_status": "复核结果只能是已满足或未满足",
        "teacher_role_required": "需要教师课程成员身份",
        "object_access_denied": "无权访问该对象",
    }
    status = 403 if code in {
        "student_submit_required", "teacher_role_required", "object_access_denied",
        "formal_grade_not_writable", "teacher_review_required",
    } else 409
    return HTTPException(status, detail={"code": code, "message": messages.get(code, code)})


async def _submission_for(session: AsyncSession, *, submission_id: str, user: User) -> tuple[Submission, Attempt]:
    submission = await session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(404, "submission_not_found")
    attempt = await _attempt(session, attempt_id=submission.attempt_id, user=user)
    return submission, attempt


def _snapshot_files(manager: AttemptWorkspaceManager, *, attempt_id: str, snapshot_id: str) -> list[dict[str, Any]]:
    root = manager.snapshot_directory(attempt_id, snapshot_id)
    files = []
    if not root.exists():
        return files
    for path in sorted(item for item in root.rglob("*") if item.is_file() and not item.is_symlink()):
        if path.suffix.lower() not in ALLOWED_FILE_SUFFIXES:
            continue
        files.append({"path": path.relative_to(root).as_posix(), "name": path.name, "size": path.stat().st_size})
    return files


@router.post("/attempts/{attempt_id}/submissions")
async def create_submission(
    attempt_id: str,
    body: SubmissionCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    version = await session.get(TaskVersion, attempt.task_version_id)
    task = await session.get(Task, version.task_id) if version else None
    pack = task_pack_for(f"{task.task_key}-{version.version}") if task and version else None
    if pack is not None:
        latest = await _latest_snapshot(session, attempt.id)
        if latest is None or latest.id != body.snapshot_id:
            raise HTTPException(409, detail={"code": "old_snapshot", "message": "代码版本已变化，请返回工作台重新保存。"})
        results = list((await session.execute(
            select(RequirementResult, RequirementDefinition.requirement_key)
            .join(RequirementDefinition, RequirementDefinition.id == RequirementResult.requirement_id)
            .where(RequirementResult.attempt_id == attempt.id, RequirementResult.snapshot_id == latest.id)
            .order_by(RequirementResult.evaluated_at, RequirementResult.id)
        )).all())
        current = {key: result for result, key in results}
        if current.get(pack.public_requirement) is None or current[pack.public_requirement].status != "SATISFIED":
            raise HTTPException(409, detail={"code": "public_check_required", "message": "请先返回工作台，让当前代码通过公开样例检查。"})
        if current.get(pack.hidden_requirement) is None or current[pack.hidden_requirement].status != "SATISFIED":
            try:
                hidden = await _runtime(request).run_event(
                    attempt_id=attempt.id,
                    event=_graph_event(
                        attempt=attempt, user=user,
                        body=RunCreate(operation_id=f"submit-hidden:{body.operation_id}",
                                       snapshot_id=body.snapshot_id,
                                       expected_state_version=attempt.state_version),
                        event_type="run_tool", task_version=f"{task.task_key}-{version.version}",
                        requested_tool="run_python_hidden_tests",
                    ),
                    automatic_followup=False,
                )
            except BusinessRuleError as exc:
                raise _product_fail(exc) from exc
            except Exception as exc:
                raise HTTPException(503, detail={"code": "execution_unavailable", "message": "提交检查环境暂不可用，当前代码和公开检查结果已保存。"}) from exc
            status = hidden.get("last_tool_status")
            if status == "student_failure":
                raise HTTPException(409, detail={"code": "hidden_check_failed", "message": "边界检查未通过。请回到代码，核对程序是否适用于题面范围内的所有整数。"})
            if status != "succeeded":
                raise HTTPException(503, detail={"code": "execution_unavailable", "message": "提交检查暂不可用，当前代码和公开检查结果已保存。"})
            hidden_result = await session.scalar(
                select(RequirementResult)
                .join(RequirementDefinition, RequirementDefinition.id == RequirementResult.requirement_id)
                .where(RequirementResult.attempt_id == attempt.id,
                       RequirementResult.snapshot_id == latest.id,
                       RequirementDefinition.requirement_key == pack.hidden_requirement)
                .order_by(RequirementResult.evaluated_at.desc(), RequirementResult.id.desc())
                .limit(1)
            )
            if hidden_result is None or hidden_result.status != "SATISFIED":
                raise HTTPException(503, detail={"code": "hidden_evidence_missing", "message": "边界检查未生成可核对证据，当前代码已保存，请稍后重试。"})
            await session.refresh(attempt)
    try:
        submission, duplicate = await reviews.create_submission(
            session,
            attempt=attempt,
            user=user,
            snapshot_id=body.snapshot_id,
            operation_id=body.operation_id,
            explanation=body.explanation,
        )
    except BusinessRuleError as exc:
        raise _product_fail(exc) from exc
    snapshot = await session.get(Snapshot, submission.snapshot_id)
    return {
        "id": submission.id,
        "duplicate": duplicate,
        "sequence": submission.sequence,
        "snapshot_id": submission.snapshot_id,
        "snapshot_label": _snapshot_label(snapshot),
        "created_at": submission.created_at.isoformat(),
    }


@router.get("/attempts/{attempt_id}/submissions/latest")
async def latest_submission_view(
    attempt_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
    preview: bool = False,
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    submission = None if preview else await reviews.latest_submission(session, attempt_id=attempt.id)
    if submission is None:
        latest = await _latest_snapshot(session, attempt.id)
        if latest is None:
            raise HTTPException(409, detail={"code": "snapshot_required", "message": "请先保存并创建检查版本，再提交。"})
        preview = Submission(
            id="",
            attempt_id=attempt.id,
            snapshot_id=latest.id,
            operation_id="",
            sequence=0,
            status="preview",
            explanation=attempt and "",
            assistance=await reviews.assistance_history(session, attempt_id=attempt.id),
        )
        preview.explanation = next((
            str((event.payload or {}).get("student_observation"))
            for event in reversed(list((await session.scalars(
                select(TeachingEvent).where(TeachingEvent.attempt_id == attempt.id).order_by(TeachingEvent.created_at)
            )).all()))
            if (event.payload or {}).get("student_observation")
        ), "")
        view = await reviews.evaluation_view(session, submission=preview, teacher=None)
        view["submission"]["id"] = None
        view["submission"]["status"] = "preview"
        view["files"] = _snapshot_files(_manager(request), attempt_id=attempt.id, snapshot_id=latest.id)
        return view
    view = await reviews.evaluation_view(
        session,
        submission=submission,
        teacher=user if attempt.learner_id != user.id else None,
    )
    view["files"] = _snapshot_files(_manager(request), attempt_id=attempt.id, snapshot_id=submission.snapshot_id)
    return view


@router.get("/submissions/{submission_id}")
async def submission_view(
    submission_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    submission, attempt = await _submission_for(session, submission_id=submission_id, user=user)
    view = await reviews.evaluation_view(
        session,
        submission=submission,
        teacher=user if attempt.learner_id != user.id else None,
    )
    view["files"] = _snapshot_files(_manager(request), attempt_id=attempt.id, snapshot_id=submission.snapshot_id)
    return view


@router.get("/submissions/{submission_id}/files/{file_path:path}")
async def read_submission_file(
    submission_id: str,
    file_path: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    submission, attempt = await _submission_for(session, submission_id=submission_id, user=user)
    root = _manager(request).snapshot_directory(attempt.id, submission.snapshot_id)
    path = _relative_file(root, file_path)
    if not path.is_file():
        raise HTTPException(404, "file_not_found")
    content = path.read_text(encoding="utf-8")
    return {"path": file_path, "content": content, "hash": _file_hash(content), "size": path.stat().st_size}


@router.put("/teacher/reviews/{review_id}")
async def save_review_draft(
    review_id: str,
    body: ReviewDraftSave,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    review = await session.get(Review, review_id)
    if review is None:
        raise HTTPException(404, "review_not_found")
    submission = await session.get(Submission, review.submission_id)
    attempt = await _attempt(session, attempt_id=submission.attempt_id, user=user)
    if attempt.learner_id == user.id:
        raise HTTPException(403, detail={"code": "teacher_role_required", "message": "学生不能填写正式评价"})
    try:
        saved = await reviews.save_draft(
            session,
            review=review,
            teacher=user,
            items=[item.model_dump() for item in body.items],
        )
    except BusinessRuleError as exc:
        raise _product_fail(exc) from exc
    view = await reviews.evaluation_view(session, submission=submission, teacher=user)
    view["review"]["id"] = saved.id
    return view


@router.post("/teacher/reviews/{review_id}/publish")
async def publish_review(
    review_id: str,
    body: ReviewPublish,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    review = await session.get(Review, review_id)
    if review is None:
        raise HTTPException(404, "review_not_found")
    submission = await session.get(Submission, review.submission_id)
    attempt = await _attempt(session, attempt_id=submission.attempt_id, user=user)
    if attempt.learner_id == user.id:
        raise HTTPException(403, detail={"code": "teacher_role_required", "message": "学生不能发布正式成绩"})
    try:
        grade = await reviews.publish(
            session,
            review=review,
            teacher=user,
            operation_id=body.operation_id,
            change_reason=body.change_reason,
        )
    except BusinessRuleError as exc:
        raise _product_fail(exc) from exc
    return {
        "submission_id": submission.id,
        "review_id": review.id,
        "formal_grade": {
            "total_score": grade.total_score,
            "max_score": grade.max_score,
            "published_at": grade.published_at.isoformat(),
            "published_by": user.display_name,
        },
    }


@router.post("/teacher/submissions/{submission_id}/requirement-reviews")
async def teacher_requirement_review(
    submission_id: str,
    body: TeacherRequirementReview,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    submission, attempt = await _submission_for(session, submission_id=submission_id, user=user)
    if attempt.learner_id == user.id:
        raise HTTPException(403, detail={"code": "teacher_role_required", "message": "学生不能确认教师复核项"})
    try:
        result = await reviews.record_teacher_requirement(
            session,
            attempt=attempt,
            submission=submission,
            teacher=user,
            requirement_key=body.requirement_key,
            status=body.status,
            operation_id=body.operation_id,
            reason=body.reason,
        )
    except BusinessRuleError as exc:
        raise _product_fail(exc) from exc
    return {
        "requirement_key": body.requirement_key,
        "status": result.status,
        "snapshot_id": result.snapshot_id,
        "evaluator": result.evaluator,
        "operation_id": result.operation_id,
    }


class LearningMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    expected_state_version: int
    message: str = Field(min_length=1, max_length=1200)


async def _learning_message(session, *, attempt, user, body, event_type):
    existing = await session.scalar(select(TeachingEvent).where(
        TeachingEvent.attempt_id == attempt.id, TeachingEvent.operation_id == body.operation_id,
    ))
    if existing:
        if existing.event_type != event_type or existing.payload.get("message") != body.message.strip():
            raise HTTPException(409, "operation_payload_conflict")
        return {"state_version": existing.state_version, "message": existing.payload.get("message")}
    active = await session.scalar(select(Intervention).where(
        Intervention.attempt_id == attempt.id,
        Intervention.status.in_(["CREATING", "CHECKPOINT_FAILED", "WAITING_TEACHER", "RESUMING", "RESUME_FAILED"]),
    ).limit(1))
    if active:
        raise HTTPException(409, {"code": "teacher_intervention_pending", "message": "已有教师介入事项，请在介入操作中处理。"})
    message = body.message.strip()
    if not message:
        raise HTTPException(422, {"code": "message_required", "message": "请写下需要帮助的问题或指导内容。"})
    if event_type == "help_requested":
        recent = await session.scalar(select(TeachingEvent).where(
            TeachingEvent.attempt_id == attempt.id,
            TeachingEvent.event_type.in_(["help_requested", "teacher_feedback"]),
        ).order_by(TeachingEvent.created_at.desc()).limit(1))
        if recent and recent.event_type == "help_requested":
            return {"state_version": attempt.state_version, "message": recent.payload.get("message")}
    stage = await session.get(TaskStage, attempt.current_stage_id)
    snapshot = await _latest_snapshot(session, attempt.id)
    try:
        event, _ = await service.record_event(session, attempt=attempt, actor_id=user.id,
            operation_id=body.operation_id, event_type=event_type,
            expected_state_version=body.expected_state_version,
            payload={"message": message, "stage": stage.stage_key,
                     "student_observation": message if event_type == "help_requested" else "",
                     "snapshot_id": snapshot.id if snapshot else None})
    except BusinessRuleError as exc:
        raise _product_fail(exc) from exc
    return {"state_version": event.state_version, "message": message}


@router.post("/attempts/{attempt_id}/help")
async def request_teacher_help(attempt_id: str, body: LearningMessage,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)]):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id != user.id:
        raise HTTPException(403, "student_help_required")
    return await _learning_message(session, attempt=attempt, user=user, body=body, event_type="help_requested")


@router.post("/teacher/attempts/{attempt_id}/feedback")
async def send_teacher_feedback(attempt_id: str, body: LearningMessage,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)]):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id == user.id or user.system_role not in {"teacher", "admin"}:
        raise HTTPException(403, "teacher_role_required")
    return await _learning_message(session, attempt=attempt, user=user, body=body, event_type="teacher_feedback")


class StageReview(TeacherRequirementReview):
    snapshot_id: str


@router.post("/teacher/attempts/{attempt_id}/stage-reviews")
async def review_current_stage(attempt_id: str, body: StageReview,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)]):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    if attempt.learner_id == user.id or user.system_role not in {"teacher", "admin"}:
        raise HTTPException(403, "teacher_role_required")
    latest = await _latest_snapshot(session, attempt.id)
    if latest is None or latest.id != body.snapshot_id:
        raise HTTPException(409, {"code": "old_snapshot", "message": "学生代码版本已更新，请重新查看后复核。"})
    definition = await session.scalar(select(RequirementDefinition).where(
        RequirementDefinition.task_stage_id == attempt.current_stage_id,
        RequirementDefinition.requirement_key == body.requirement_key,
        RequirementDefinition.kind == "TEACHER_REVIEW",
    ))
    if definition is None or body.status not in {"SATISFIED", "NOT_SATISFIED"}:
        raise HTTPException(422, "teacher_review_required")
    try:
        record = await service.upsert_requirement_result(session, attempt=attempt,
            requirement_id=definition.id, snapshot_id=latest.id,
            operation_id=f"stage-review:{body.operation_id}"[:160], status=body.status,
            evaluator="teacher_review_v1", evidence_refs=[f"teacher://{user.id}"], version=definition.version,
            commit=False)
        await _learning_message(session, attempt=attempt, user=user,
            body=LearningMessage(operation_id=f"feedback:{body.operation_id}"[:160],
                                 expected_state_version=attempt.state_version,
                                 message=f"{REQUIREMENT_NAMES.get(body.requirement_key, body.requirement_key)}：{'通过' if body.status == 'SATISFIED' else '需要修改'}。{body.reason}"),
            event_type="teacher_feedback")
    except BusinessRuleError as exc:
        raise _product_fail(exc) from exc
    return {"id": record.id, "status": record.status, "snapshot_id": record.snapshot_id}
