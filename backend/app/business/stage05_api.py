"""Stage 05A product views over the existing business facts and teaching runtime."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.workspace import AttemptWorkspaceManager
from app.teaching_control.state import TeachingEvent as GraphEvent

from .api import current_user, db_session, fail
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
from .reviews import ReviewService
from .service import BusinessRuleError, BusinessService

router = APIRouter(prefix="/api/product", tags=["stage05a-product"])
service = BusinessService()
reviews = ReviewService(service)

REQUIREMENT_NAMES = {
    "explain_scope": "说明任务边界",
    "identify_citation_rule": "识别引用规则",
    "source_manifest": "资料正常加载",
    "source_quality_review": "资料质量复核",
    "retrieval_public_tests": "已知问题能够命中",
    "retrieval_observation": "已提交调试观察",
    "answer_public_tests": "回答符合基础测试",
    "citation_static_check": "回答包含来源引用",
    "unknown_question_test": "未知问题不伪造命中",
    "boundary_transfer": "完成边界迁移任务",
    "delivery_static_check": "交付文件完整",
    "delivery_review": "教师完成交付复核",
}
STAGE_OBJECTIVES = {
    "understand_requirements": "说明 FAQ 服务要解决的问题，并明确回答必须带来源。",
    "prepare_sources": "准备结构清晰、可追溯的 FAQ 资料。",
    "implement_retrieval": "让已知问题命中资料，同时让未知问题保持无结果。",
    "generate_cited_answer": "基于命中资料生成带来源的回答。",
    "validate_boundaries": "验证无法回答的问题不会被模型伪造。",
    "deliver": "整理运行说明与可验收交付物。",
}
ALLOWED_FILE_SUFFIXES = {".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}
MAX_FILE_BYTES = 512 * 1024


class FileSave(BaseModel):
    content: str = Field(max_length=MAX_FILE_BYTES)
    expected_hash: str


class SnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    expected_file_hash: str | None = None
    expected_file_path: str = Field(default="faq_app.py", min_length=1, max_length=240)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=160)
    snapshot_id: str
    expected_state_version: int
    observation: str = Field(default="", max_length=1200)


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
    return clean


def _event_label(event: TeachingEvent) -> str:
    payload = event.payload or {}
    event_type = payload.get("event_type") or event.event_type
    decision = payload.get("decision")
    if event_type == "stage_advanced":
        return "阶段验收通过，进入下一阶段"
    if decision == "execute_tool":
        status = payload.get("check_status")
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
        .order_by(RequirementDefinition.id)
    )).all())
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
    for event in events:
        payload = event.payload or {}
        decision = payload.get("decision")
        if decision == "execute_tool":
            trace.extend([
                {"kind": "student", "label": "学生行为", "detail": "提交当前 Snapshot 并运行验收"},
                {"kind": "openhands", "label": "OpenHands 实训证据", "detail": _event_label(event)},
                {"kind": "evaluator", "label": "RequirementEvaluator", "detail": "按 Snapshot 聚合版本化验收结果"},
                {"kind": "langgraph", "label": "LangGraph 教学决策", "detail": f"服务端路由：{decision}"},
            ])
        elif decision == "generate_guidance":
            guidance = payload.get("guidance") or {}
            trace.append({
                "kind": "deepseek", "label": "DeepSeek 教学表达",
                "detail": guidance.get("message") or "使用安全模板指导",
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
            "objective": stage_objectives.get(
                current_stage.stage_key,
                STAGE_OBJECTIVES.get(current_stage.stage_key, "完成本阶段验收项。"),
            ),
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
                "name": (definition.config or {}).get("display_name")
                or requirement_names.get(definition.requirement_key)
                or REQUIREMENT_NAMES.get(definition.requirement_key, definition.requirement_key),
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
        "guidance": latest_guidance,
        "guidance_history": [
            {"time": event.created_at.isoformat(), **_safe_payload(event.payload or {}).get("guidance", {})}
            for event in guidance_events
        ],
        "student_observation": next((
            str((event.payload or {}).get("student_observation"))
            for event in reversed(events) if (event.payload or {}).get("student_observation")
        ), ""),
        "intervention": (
            {"id": interventions[0].id, "status": interventions[0].status,
             "reason": interventions[0].reason, "created_at": interventions[0].created_at.isoformat()}
            if interventions else None
        ),
        "timeline": timeline,
        "agent_trace": trace[-12:],
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
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if _file_hash(current) != body.expected_hash:
        raise HTTPException(409, detail={"code": "snapshot_conflict", "message": "文件已在其他位置更新，请重新载入后再保存"})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".saving")
    temporary.write_text(body.content, encoding="utf-8")
    temporary.replace(path)
    return {"path": file_path, "hash": _file_hash(body.content), "saved_at": datetime.now(UTC).isoformat()}


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
    body: RunCreate | GuidanceCreate,
    event_type: str,
    task_version: str,
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
        "student_observation": body.observation.strip(),
        "failure_origin": "none",
        "requested_guidance_kind": "question",
        "requested_tool": "run_faq_tests" if event_type == "run_tool" else "",
    }


async def _snapshot_requirement_evidence(
    session: AsyncSession, *, attempt_id: str, snapshot_id: str
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
    try:
        state = await _runtime(request).run_event(
            attempt_id=attempt.id,
            event=_graph_event(
                attempt=attempt,
                user=user,
                body=body,
                event_type="run_tool",
                task_version=f"{task.task_key}-{version.version}",
            ),
            automatic_followup=True,
        )
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    except Exception as exc:
        raise HTTPException(503, detail={"code": "openhands_unavailable", "message": "实训执行服务暂不可用"}) from exc
    return {
        "flow_status": state.get("flow_status"),
        "last_tool_status": state.get("last_tool_status"),
        "help_level": state.get("help_level"),
        "guidance": state.get("guidance"),
        "stage_assessment": state.get("stage_assessment"),
        "error_code": state.get("error_code"),
        "state_version": state.get("state_version"),
        "snapshot_id": state.get("latest_snapshot_id"),
    }


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
        session, attempt_id=attempt.id, snapshot_id=body.snapshot_id
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
        learner = await session.get(User, attempt.learner_id)
        stage = await session.get(TaskStage, attempt.current_stage_id)
        intervention = await session.scalar(select(Intervention).where(
            Intervention.attempt_id == attempt.id
        ).order_by(Intervention.created_at.desc()).limit(1))
        submission = await reviews.latest_submission(session, attempt_id=attempt.id)
        grade = await session.scalar(select(FormalGrade).where(FormalGrade.submission_id == submission.id)) if submission else None
        waiting = intervention and intervention.status in {"WAITING_TEACHER", "RESUME_FAILED"}
        pending_review = submission is not None and grade is None
        if attempt.status == "completed" or grade is not None:
            category = "completed"
        elif waiting or pending_review:
            category = "attention"
        else:
            category = "progress"
        reason = "正常学习中"
        if waiting:
            reason = intervention.reason
        elif pending_review:
            reason = "pending_review"
        elif grade is not None:
            reason = "grade_published"
        wait_from = intervention.created_at if waiting else (submission.created_at if submission else None)
        if wait_from is not None and wait_from.tzinfo is None:
            wait_from = wait_from.replace(tzinfo=UTC)
        wait_seconds = int((now - wait_from).total_seconds()) if category == "attention" and wait_from is not None else 0
        items.append({
            "attempt_id": attempt.id,
            "student": learner.display_name if learner else "未知学生",
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
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
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
):
    attempt = await _attempt(session, attempt_id=attempt_id, user=user)
    submission = await reviews.latest_submission(session, attempt_id=attempt.id)
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
            assistance=[],
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
