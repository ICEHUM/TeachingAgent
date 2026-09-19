from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .faq import DEFAULT_TASK_POLICY, FAQ_STAGES, FAQ_TASK_KEY, FAQ_VERSION
from .models import (
    Attempt,
    CourseMembership,
    Intervention,
    OperationLedger,
    RequirementDefinition,
    RequirementResult,
    Task,
    TaskStage,
    TaskVersion,
    TeachingEvent,
)
from .requirements import RequirementEvaluator


class BusinessRuleError(RuntimeError):
    pass


class BusinessService:
    def __init__(self, evaluator: RequirementEvaluator | None = None):
        self.evaluator = evaluator or RequirementEvaluator()

    async def _membership(self, session: AsyncSession, *, course_id: str, user_id: str) -> CourseMembership:
        membership = await session.scalar(select(CourseMembership).where(
            CourseMembership.course_id == course_id, CourseMembership.user_id == user_id
        ))
        if membership is None:
            raise BusinessRuleError("course_membership_required")
        return membership

    async def authorize_attempt(self, session: AsyncSession, *, attempt: Attempt, user_id: str, teacher: bool = False) -> None:
        if not teacher and attempt.learner_id == user_id:
            return
        version = await session.get(TaskVersion, attempt.task_version_id)
        task = await session.get(Task, version.task_id) if version else None
        if task is None:
            raise BusinessRuleError("task_not_found")
        membership = await self._membership(session, course_id=task.course_id, user_id=user_id)
        if teacher and membership.role in {"teacher", "owner"}:
            return
        raise BusinessRuleError("object_access_denied")

    async def create_faq_version(self, session: AsyncSession, *, course_id: str, actor_id: str) -> TaskVersion:
        membership = await self._membership(session, course_id=course_id, user_id=actor_id)
        if membership.role not in {"teacher", "owner"}:
            raise BusinessRuleError("teacher_role_required")
        task = await session.scalar(select(Task).where(Task.course_id == course_id, Task.task_key == FAQ_TASK_KEY))
        if task is None:
            task = Task(course_id=course_id, task_key=FAQ_TASK_KEY, title="带来源引用的 FAQ 问答服务")
            session.add(task); await session.flush()
        existing = await session.scalar(select(TaskVersion).where(TaskVersion.task_id == task.id, TaskVersion.version == FAQ_VERSION))
        if existing:
            return existing
        version = TaskVersion(task_id=task.id, version=FAQ_VERSION, policy=dict(DEFAULT_TASK_POLICY))
        session.add(version); await session.flush()
        for position, (key, title, requirements) in enumerate(FAQ_STAGES):
            stage = TaskStage(task_version_id=version.id, stage_key=key, title=title, position=position)
            session.add(stage); await session.flush()
            for req_key, kind, evaluator, config in requirements:
                session.add(RequirementDefinition(
                    task_stage_id=stage.id, requirement_key=req_key, kind=kind,
                    required=True, version=1, evaluator=evaluator, config=config,
                ))
        await session.commit()
        return version

    async def create_attempt(self, session: AsyncSession, *, task_version_id: str, learner_id: str, mode: str) -> Attempt:
        version = await session.get(TaskVersion, task_version_id)
        task = await session.get(Task, version.task_id) if version else None
        if task is None:
            raise BusinessRuleError("task_version_not_found")
        membership = await self._membership(session, course_id=task.course_id, user_id=learner_id)
        if membership.role not in {"student", "teacher", "owner"}:
            raise BusinessRuleError("student_role_required")
        first = await session.scalar(select(TaskStage).where(TaskStage.task_version_id == version.id).order_by(TaskStage.position))
        if first is None:
            raise BusinessRuleError("task_has_no_stages")
        attempt = Attempt(task_version_id=version.id, learner_id=learner_id, current_stage_id=first.id, mode=mode)
        session.add(attempt); await session.commit()
        return attempt

    async def record_event(self, session: AsyncSession, *, attempt: Attempt, actor_id: str, operation_id: str, event_type: str, expected_state_version: int, payload: dict) -> tuple[TeachingEvent, bool]:
        existing = await session.scalar(select(TeachingEvent).where(
            TeachingEvent.attempt_id == attempt.id, TeachingEvent.operation_id == operation_id
        ))
        if existing:
            return existing, True
        if attempt.state_version != expected_state_version:
            raise BusinessRuleError("stale_state_version")
        # Deliberately discard client-computed authority fields.
        clean_payload = {k: v for k, v in payload.items() if k not in {"stage_requirements_met", "formal_grade"}}
        new_version = expected_state_version + 1
        result = await session.execute(update(Attempt).where(
            Attempt.id == attempt.id, Attempt.state_version == expected_state_version
        ).values(state_version=new_version))
        if result.rowcount != 1:
            raise BusinessRuleError("stale_state_version")
        event = TeachingEvent(attempt_id=attempt.id, actor_id=actor_id, event_type=event_type,
                              operation_id=operation_id, state_version=new_version, payload=clean_payload)
        ledger = OperationLedger(scope=f"attempt:{attempt.id}", operation_id=operation_id,
                                 result_ref=f"event:{event.id}", result_payload={"state_version": new_version})
        session.add_all([event, ledger]); await session.commit(); await session.refresh(event)
        return event, False

    async def upsert_requirement_result(self, session: AsyncSession, *, attempt: Attempt, requirement_id: str,
                                        status: str, evaluator: str, evidence_refs: list[str], version: int) -> RequirementResult:
        definition = await session.get(RequirementDefinition, requirement_id)
        if definition is None or definition.version != version:
            raise BusinessRuleError("requirement_version_mismatch")
        existing = await session.scalar(select(RequirementResult).where(
            RequirementResult.attempt_id == attempt.id,
            RequirementResult.requirement_id == requirement_id,
            RequirementResult.version == version,
        ))
        if existing:
            existing.status = status; existing.evaluator = evaluator; existing.evidence_refs = evidence_refs
            existing.evaluated_at = datetime.now(UTC)
            result = existing
        else:
            result = RequirementResult(attempt_id=attempt.id, requirement_id=requirement_id, status=status,
                                       evaluator=evaluator, evidence_refs=evidence_refs, version=version)
            session.add(result)
        await session.commit(); await session.refresh(result); return result

    async def create_intervention(self, session: AsyncSession, *, attempt: Attempt, operation_id: str,
                                  reason: str, evidence_refs: list[str]) -> tuple[Intervention, bool]:
        existing = await session.scalar(select(Intervention).where(Intervention.operation_id == operation_id))
        if existing:
            return existing, True
        intervention = Intervention(attempt_id=attempt.id, operation_id=operation_id, reason=reason,
                                    requested_state_version=attempt.state_version, evidence_refs=evidence_refs)
        session.add(intervention)
        session.add(OperationLedger(scope="intervention", operation_id=operation_id,
                                    result_ref=f"intervention:{intervention.id}"))
        await session.commit(); await session.refresh(intervention)
        return intervention, False

    async def resolve_intervention(self, session: AsyncSession, *, intervention_id: str, teacher_id: str,
                                   expected_state_version: int, response: str, allow_l2: bool) -> Intervention:
        intervention = await session.get(Intervention, intervention_id)
        if intervention is None or intervention.status != "pending":
            raise BusinessRuleError("pending_intervention_not_found")
        attempt = await session.get(Attempt, intervention.attempt_id)
        if attempt is None:
            raise BusinessRuleError("attempt_not_found")
        await self.authorize_attempt(session, attempt=attempt, user_id=teacher_id, teacher=True)
        if attempt.state_version != expected_state_version or intervention.requested_state_version != expected_state_version:
            raise BusinessRuleError("stale_state_version")
        intervention.status = "resolved"; intervention.assigned_teacher_id = teacher_id
        intervention.response = response; intervention.allow_l2 = allow_l2
        intervention.resolved_at = datetime.now(UTC)
        await session.commit(); return intervention
