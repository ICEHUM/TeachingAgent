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


INTERVENTION_CREATING = "CREATING"
INTERVENTION_WAITING = "WAITING_TEACHER"
INTERVENTION_RESUMING = "RESUMING"
INTERVENTION_RESOLVED = "RESOLVED"
INTERVENTION_CHECKPOINT_FAILED = "CHECKPOINT_FAILED"
INTERVENTION_RESUME_FAILED = "RESUME_FAILED"


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
                                    status=INTERVENTION_CREATING,
                                    requested_state_version=attempt.state_version,
                                    evidence_refs=evidence_refs)
        session.add(intervention)
        session.add(OperationLedger(scope="intervention", operation_id=operation_id,
                                    status="COMPLETED",
                                    result_ref=f"intervention:{intervention.id}",
                                    result_payload={"status": INTERVENTION_CREATING}))
        await session.commit(); await session.refresh(intervention)
        return intervention, False

    async def mark_checkpoint_result(
        self,
        session: AsyncSession,
        *,
        intervention_id: str,
        succeeded: bool,
        error_code: str | None = None,
    ) -> Intervention:
        intervention = await session.get(Intervention, intervention_id)
        if intervention is None:
            raise BusinessRuleError("intervention_not_found")
        if succeeded:
            if intervention.status in {INTERVENTION_CREATING, INTERVENTION_CHECKPOINT_FAILED}:
                intervention.status = INTERVENTION_WAITING
        elif intervention.status in {INTERVENTION_CREATING, INTERVENTION_CHECKPOINT_FAILED}:
            intervention.status = INTERVENTION_CHECKPOINT_FAILED
        ledger = await session.scalar(select(OperationLedger).where(
            OperationLedger.scope == "intervention",
            OperationLedger.operation_id == intervention.operation_id,
        ))
        if ledger is not None:
            ledger.result_payload = {
                "status": intervention.status,
                "checkpoint_error": error_code,
            }
        await session.commit()
        await session.refresh(intervention)
        return intervention

    async def prepare_checkpoint_retry(
        self, session: AsyncSession, *, intervention_id: str
    ) -> Intervention:
        intervention = await session.get(Intervention, intervention_id)
        if intervention is None:
            raise BusinessRuleError("intervention_not_found")
        if intervention.status == INTERVENTION_CHECKPOINT_FAILED:
            intervention.status = INTERVENTION_CREATING
            await session.commit()
            await session.refresh(intervention)
        elif intervention.status not in {INTERVENTION_CREATING, INTERVENTION_WAITING}:
            raise BusinessRuleError("checkpoint_retry_not_allowed")
        return intervention

    async def list_waiting_interventions(
        self, session: AsyncSession
    ) -> list[Intervention]:
        return list((await session.scalars(
            select(Intervention)
            .where(Intervention.status == INTERVENTION_WAITING)
            .order_by(Intervention.created_at)
        )).all())

    async def begin_resume(
        self,
        session: AsyncSession,
        *,
        intervention_id: str,
        teacher_id: str,
        resume_operation_id: str,
        expected_state_version: int,
        response: str,
        allow_l2: bool,
    ) -> tuple[Intervention, bool]:
        intervention = await session.get(Intervention, intervention_id)
        if intervention is None:
            raise BusinessRuleError("intervention_not_found")
        attempt = await session.get(Attempt, intervention.attempt_id)
        if attempt is None:
            raise BusinessRuleError("attempt_not_found")
        await self.authorize_attempt(session, attempt=attempt, user_id=teacher_id, teacher=True)
        if attempt.state_version != expected_state_version or intervention.requested_state_version != expected_state_version:
            raise BusinessRuleError("stale_state_version")
        task_version = await session.get(TaskVersion, attempt.task_version_id)
        if task_version is None:
            raise BusinessRuleError("task_version_not_found")
        policy = dict(task_version.policy or {})
        if allow_l2 and (
            attempt.mode == "assessment" or policy.get("max_help_level") == "L0"
        ):
            raise BusinessRuleError("l2_forbidden_by_current_policy")

        scope = f"intervention-resume:{intervention.id}"
        existing = await session.scalar(select(OperationLedger).where(
            OperationLedger.scope == scope,
            OperationLedger.operation_id == resume_operation_id,
        ))
        if existing is not None:
            if intervention.assigned_teacher_id != teacher_id:
                raise BusinessRuleError("resume_operation_owner_mismatch")
            if intervention.status == INTERVENTION_RESUME_FAILED:
                intervention.status = INTERVENTION_RESUMING
                existing.status = "RUNNING"
                existing.result_payload = {"status": INTERVENTION_RESUMING}
                await session.commit()
                await session.refresh(intervention)
            elif intervention.status not in {INTERVENTION_RESUMING, INTERVENTION_RESOLVED}:
                raise BusinessRuleError("resume_retry_not_allowed")
            return intervention, True

        if intervention.status != INTERVENTION_WAITING:
            raise BusinessRuleError("waiting_intervention_not_found")
        intervention.status = INTERVENTION_RESUMING
        intervention.assigned_teacher_id = teacher_id
        intervention.response = response
        intervention.allow_l2 = allow_l2
        session.add(OperationLedger(
            scope=scope,
            operation_id=resume_operation_id,
            status="RUNNING",
            result_ref=f"intervention:{intervention.id}",
            result_payload={"status": INTERVENTION_RESUMING},
        ))
        await session.commit()
        await session.refresh(intervention)
        return intervention, False

    async def finish_resume(
        self,
        session: AsyncSession,
        *,
        intervention_id: str,
        resume_operation_id: str,
        succeeded: bool,
        error_code: str | None = None,
    ) -> Intervention:
        intervention = await session.get(Intervention, intervention_id)
        if intervention is None:
            raise BusinessRuleError("intervention_not_found")
        scope = f"intervention-resume:{intervention.id}"
        ledger = await session.scalar(select(OperationLedger).where(
            OperationLedger.scope == scope,
            OperationLedger.operation_id == resume_operation_id,
        ))
        if ledger is None:
            raise BusinessRuleError("resume_operation_not_found")
        if succeeded:
            if intervention.status == INTERVENTION_RESUMING:
                intervention.status = INTERVENTION_RESOLVED
                intervention.resolved_at = datetime.now(UTC)
            elif intervention.status != INTERVENTION_RESOLVED:
                raise BusinessRuleError("resume_completion_not_allowed")
            ledger.status = "COMPLETED"
        else:
            if intervention.status == INTERVENTION_RESUMING:
                intervention.status = INTERVENTION_RESUME_FAILED
            elif intervention.status != INTERVENTION_RESUME_FAILED:
                raise BusinessRuleError("resume_failure_not_allowed")
            ledger.status = "FAILED"
        ledger.result_payload = {"status": intervention.status, "error": error_code}
        await session.commit()
        await session.refresh(intervention)
        return intervention
