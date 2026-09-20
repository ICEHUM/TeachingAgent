from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Attempt,
    Intervention,
    RequirementResult,
    TaskStage,
    TaskVersion,
    TeachingEvent,
    User,
)
from .service import BusinessRuleError, BusinessService

router = APIRouter(prefix="/api")
service = BusinessService()


async def db_session(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


async def current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    x_user_id: Annotated[str, Header(alias="X-User-Id")],
) -> User:
    if not request.app.state.dev_auth_enabled:
        raise HTTPException(401, "dev_auth_disabled")
    user = await session.get(User, x_user_id)
    if user is None:
        raise HTTPException(401, "unknown_user")
    return user


def fail(error: BusinessRuleError) -> HTTPException:
    status = 409 if str(error) == "stale_state_version" else 403
    return HTTPException(status, str(error))


class FAQVersionCreate(BaseModel):
    course_id: str


class AttemptCreate(BaseModel):
    task_version_id: str
    mode: str = "guided_practice"


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    operation_id: str
    event_type: str
    expected_state_version: int
    payload: dict[str, Any] = Field(default_factory=dict)
    stage_requirements_met: bool | None = None  # accepted but intentionally ignored


class InterventionCreate(BaseModel):
    operation_id: str
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)


class InterventionResolve(BaseModel):
    operation_id: str
    expected_state_version: int
    response: str
    allow_l2: bool = False


@router.post("/task-versions")
async def create_task_version(body: FAQVersionCreate, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    try:
        version = await service.create_faq_version(session, course_id=body.course_id, actor_id=user.id)
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    return {"id": version.id, "task_id": version.task_id, "version": version.version, "policy": version.policy}


@router.get("/task-versions/{version_id}")
async def get_task_version(version_id: str, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    version = await session.get(TaskVersion, version_id)
    if version is None:
        raise HTTPException(404, "task_version_not_found")
    stages = list((await session.scalars(select(TaskStage).where(TaskStage.task_version_id == version.id).order_by(TaskStage.position))).all())
    return {"id": version.id, "version": version.version, "policy": version.policy,
            "stages": [{"id": s.id, "key": s.stage_key, "title": s.title, "position": s.position} for s in stages]}


@router.post("/attempts")
async def create_attempt(body: AttemptCreate, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    try:
        attempt = await service.create_attempt(session, task_version_id=body.task_version_id, learner_id=user.id, mode=body.mode)
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    return attempt_view(attempt)


def attempt_view(attempt: Attempt) -> dict[str, Any]:
    return {"id": attempt.id, "task_version_id": attempt.task_version_id, "learner_id": attempt.learner_id,
            "current_stage_id": attempt.current_stage_id, "mode": attempt.mode, "status": attempt.status,
            "state_version": attempt.state_version}


@router.get("/attempts/{attempt_id}")
async def get_attempt(attempt_id: str, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "attempt_not_found")
    try:
        await service.authorize_attempt(session, attempt=attempt, user_id=user.id, teacher=attempt.learner_id != user.id)
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    return attempt_view(attempt)


@router.post("/attempts/{attempt_id}/events")
async def submit_event(attempt_id: str, body: EventCreate, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "attempt_not_found")
    try:
        await service.authorize_attempt(session, attempt=attempt, user_id=user.id)
        event, duplicate = await service.record_event(
            session, attempt=attempt, actor_id=user.id, operation_id=body.operation_id,
            event_type=body.event_type, expected_state_version=body.expected_state_version,
            payload=body.payload,
        )
        aggregate = await service.evaluator.evaluate(session, attempt_id=attempt.id)
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    return {"event_id": event.id, "state_version": event.state_version, "duplicate": duplicate,
            "stage_requirements_met": aggregate.satisfied, "requirement_result_refs": aggregate.result_refs}


@router.get("/attempts/{attempt_id}/requirements")
async def get_requirements(attempt_id: str, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "attempt_not_found")
    await service.authorize_attempt(session, attempt=attempt, user_id=user.id, teacher=attempt.learner_id != user.id)
    results = list((await session.scalars(select(RequirementResult).where(RequirementResult.attempt_id == attempt.id))).all())
    aggregate = await service.evaluator.evaluate(session, attempt_id=attempt.id)
    return {"stage_satisfied": aggregate.satisfied, "results": [
        {"requirement_id": r.requirement_id, "status": r.status, "evaluator": r.evaluator,
         "snapshot_id": r.snapshot_id, "operation_id": r.operation_id,
         "evidence_refs": r.evidence_refs, "evaluated_at": r.evaluated_at, "version": r.version}
        for r in results]}


@router.post("/attempts/{attempt_id}/interventions")
async def create_intervention(attempt_id: str, body: InterventionCreate, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "attempt_not_found")
    await service.authorize_attempt(session, attempt=attempt, user_id=user.id, teacher=True)
    item, duplicate = await service.create_intervention(session, attempt=attempt, operation_id=body.operation_id,
                                                        reason=body.reason, evidence_refs=body.evidence_refs)
    return {"id": item.id, "status": item.status, "duplicate": duplicate}


@router.get("/interventions")
async def list_interventions(session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    if user.system_role not in {"teacher", "admin"}:
        raise HTTPException(403, "teacher_role_required")
    # Business table only. Checkpoint tables are never queried by this endpoint.
    items = await service.list_waiting_interventions(session)
    visible = []
    for item in items:
        attempt = await session.get(Attempt, item.attempt_id)
        try:
            await service.authorize_attempt(session, attempt=attempt, user_id=user.id, teacher=True)
            visible.append(item)
        except BusinessRuleError:
            pass
    return [{"id": i.id, "attempt_id": i.attempt_id, "reason": i.reason, "status": i.status,
             "requested_state_version": i.requested_state_version} for i in visible]


@router.get("/interventions/{intervention_id}")
async def get_intervention(intervention_id: str, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    item = await session.get(Intervention, intervention_id)
    if item is None:
        raise HTTPException(404, "intervention_not_found")
    attempt = await session.get(Attempt, item.attempt_id)
    await service.authorize_attempt(session, attempt=attempt, user_id=user.id, teacher=True)
    return {"id": item.id, "attempt_id": item.attempt_id, "status": item.status, "reason": item.reason,
            "requested_state_version": item.requested_state_version, "evidence_refs": item.evidence_refs}


@router.post("/interventions/{intervention_id}/resolve")
async def resolve_intervention(intervention_id: str, body: InterventionResolve, request: Request, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    runtime = getattr(request.app.state, "graph_resume_runtime", None)
    if runtime is None:
        raise HTTPException(503, "graph_resume_runtime_not_configured")
    try:
        await runtime.resume(
            intervention_id=intervention_id,
            teacher_id=user.id,
            resume_operation_id=body.operation_id,
            expected_state_version=body.expected_state_version,
            response=body.response,
            allow_l2=body.allow_l2,
        )
    except BusinessRuleError as exc:
        raise fail(exc) from exc
    except Exception as exc:
        raise HTTPException(503, "graph_resume_failed") from exc
    session.expire_all()
    item = await session.get(Intervention, intervention_id)
    if item is None:
        raise HTTPException(404, "intervention_not_found")
    return {"id": item.id, "status": item.status, "allow_l2": item.allow_l2, "graph": "resumed"}


@router.get("/attempts/{attempt_id}/events")
async def event_timeline(attempt_id: str, session: Annotated[AsyncSession, Depends(db_session)], user: Annotated[User, Depends(current_user)]):
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "attempt_not_found")
    await service.authorize_attempt(session, attempt=attempt, user_id=user.id, teacher=attempt.learner_id != user.id)
    events = list((await session.scalars(select(TeachingEvent).where(TeachingEvent.attempt_id == attempt.id).order_by(TeachingEvent.created_at))).all())
    return [{"id": e.id, "event_type": e.event_type, "operation_id": e.operation_id,
             "state_version": e.state_version, "payload": e.payload, "created_at": e.created_at} for e in events]
