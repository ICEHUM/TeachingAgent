"""Persistent Stage 03 teaching loop over business PostgreSQL and LangGraph checkpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar, cast
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tools import TOOL_CATALOG, OpenHandsExecutor, validate_execution_result
from app.teaching_control.graph import TeachingGraphDependencies, build_teaching_graph
from app.teaching_control.protocols import (
    InterventionCreation,
    PersistResult,
    RequirementEvaluation,
    ResourcePolicy,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from app.teaching_control.state import (
    TeachingEvent,
    TeachingPolicy,
    TeachingState,
    new_teaching_state,
)

from .checkpoint import checkpoint_saver
from .faq import DEFAULT_TASK_POLICY, FAQ_TASK_KEY
from .models import (
    Attempt,
    CourseMembership,
    Intervention,
    OperationLedger,
    RequirementDefinition,
    Task,
    TaskStage,
    TaskVersion,
)
from .service import (
    INTERVENTION_CHECKPOINT_FAILED,
    INTERVENTION_CREATING,
    BusinessRuleError,
    BusinessService,
)

T = TypeVar("T")


class _LoopBridge:
    """Lets LangGraph's synchronous nodes use the application's async DB loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    def call(self, awaitable: Coroutine[Any, Any, T]) -> T:
        future = asyncio.run_coroutine_threadsafe(awaitable, self.loop)
        return future.result()


@dataclass(slots=True)
class DatabaseTeachingEventStore:
    factory: async_sessionmaker[AsyncSession]
    service: BusinessService
    bridge: _LoopBridge

    async def _persist(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        expected_state_version: int,
        event: dict[str, object],
    ) -> PersistResult:
        async with self.factory() as session:
            attempt = await session.get(Attempt, attempt_id)
            if attempt is None:
                raise BusinessRuleError("attempt_not_found")
            trusted_updates: dict[str, object] = {
                "student_failure_count": int(event.get("student_failure_count", 0)),
                "infrastructure_failure_count": int(
                    event.get("infrastructure_failure_count", 0)
                ),
            }
            event_type = str(event.get("event_type", "teaching_event"))
            if event_type == "stage_advanced":
                next_stage = await session.scalar(
                    select(TaskStage).where(
                        TaskStage.task_version_id == attempt.task_version_id,
                        TaskStage.stage_key == str(event["to_stage"]),
                    )
                )
                if next_stage is None:
                    raise BusinessRuleError("target_stage_not_found")
                trusted_updates["current_stage_id"] = next_stage.id
            elif event_type == "attempt_completed":
                trusted_updates["status"] = "completed"
            record, duplicate = await self.service.record_event(
                session,
                attempt=attempt,
                actor_id=cast(str | None, event.get("actor_id")),
                operation_id=operation_id,
                event_type=event_type,
                expected_state_version=expected_state_version,
                payload=event,
                trusted_attempt_updates=trusted_updates,
            )
            observation = str(event.get("student_observation") or "").strip()
            snapshot_id = event.get("snapshot_id")
            if (
                not duplicate
                and event.get("actor_role") == "student"
                and isinstance(snapshot_id, str)
                and observation
            ):
                definition = await session.scalar(
                    select(RequirementDefinition).where(
                        RequirementDefinition.task_stage_id == attempt.current_stage_id,
                        RequirementDefinition.kind == "STUDENT_EXPLANATION",
                    )
                )
                if definition is not None:
                    minimum = int((definition.config or {}).get("min_length", 20))
                    status = "SATISFIED" if len(observation) >= minimum else "NOT_SATISFIED"
                    await self.service.upsert_requirement_result(
                        session,
                        attempt=attempt,
                        requirement_id=definition.id,
                        snapshot_id=snapshot_id,
                        operation_id=f"observation:{operation_id}",
                        status=status,
                        evaluator="server:student_explanation:v1",
                        evidence_refs=[f"event://{record.id}"],
                        version=definition.version,
                    )
            return PersistResult(
                operation_id=operation_id,
                state_version=record.state_version,
                duplicate=duplicate,
            )

    def persist(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        expected_state_version: int,
        event: dict[str, object],
    ) -> PersistResult:
        return self.bridge.call(
            self._persist(
                operation_id=operation_id,
                attempt_id=attempt_id,
                expected_state_version=expected_state_version,
                event=event,
            )
        )


@dataclass(slots=True)
class DatabaseRequirementEvaluator:
    factory: async_sessionmaker[AsyncSession]
    service: BusinessService
    bridge: _LoopBridge

    async def _evaluate(self, attempt_id: str) -> RequirementEvaluation:
        async with self.factory() as session:
            aggregate = await self.service.evaluator.evaluate(session, attempt_id=attempt_id)
            return RequirementEvaluation(aggregate.satisfied, aggregate.result_refs)

    def evaluate(
        self, *, attempt_id: str, task_version: str, stage: str
    ) -> RequirementEvaluation:
        del task_version, stage
        return self.bridge.call(self._evaluate(attempt_id))


@dataclass(slots=True)
class DatabaseInterventionStore:
    factory: async_sessionmaker[AsyncSession]
    service: BusinessService
    bridge: _LoopBridge

    async def _create(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> InterventionCreation:
        async with self.factory() as session:
            attempt = await session.get(Attempt, attempt_id)
            if attempt is None:
                raise BusinessRuleError("attempt_not_found")
            item, duplicate = await self.service.create_intervention(
                session,
                attempt=attempt,
                operation_id=operation_id,
                reason=reason,
                evidence_refs=list(evidence_refs),
            )
            return InterventionCreation(item.id, duplicate)

    def create_intervention(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        reason: str,
        requested_state_version: int,
        evidence_refs: tuple[str, ...],
    ) -> InterventionCreation:
        del requested_state_version
        return self.bridge.call(
            self._create(
                operation_id=operation_id,
                attempt_id=attempt_id,
                reason=reason,
                evidence_refs=evidence_refs,
            )
        )


REQUIREMENT_TOOL = {
    "source_manifest": "inspect_workspace",
    "retrieval_public_tests": "run_faq_tests",
    "citation_static_check": "validate_citations",
    "unknown_question_test": "run_faq_tests",
}


@dataclass(slots=True)
class DatabaseToolResultRecorder:
    factory: async_sessionmaker[AsyncSession]
    service: BusinessService
    bridge: _LoopBridge

    async def _record(
        self, request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> tuple[str, ...]:
        validate_execution_result(request, result)
        scope = f"tool:{request.attempt_id}"
        evidence_refs = tuple(
            artifact.uri
            for evidence in result.evidence
            for artifact in evidence.artifact_refs
        )
        async with self.factory() as session:
            attempt = await session.get(Attempt, request.attempt_id)
            if attempt is None:
                raise BusinessRuleError("attempt_not_found")
            duplicate = await session.scalar(
                select(OperationLedger).where(
                    OperationLedger.scope == scope,
                    OperationLedger.operation_id == request.operation_id,
                )
            )
            if duplicate is not None:
                return tuple(duplicate.result_payload.get("evidence_refs", []))
            definitions = list(
                (
                    await session.scalars(
                        select(RequirementDefinition).where(
                            RequirementDefinition.task_stage_id == attempt.current_stage_id
                        )
                    )
                ).all()
            )
            matching = [
                item
                for item in definitions
                if REQUIREMENT_TOOL.get(item.requirement_key) == request.tool_name
                or (
                    item.requirement_key == "retrieval_public_tests"
                    and request.tool_name == "validate_retrieval"
                )
            ]
            if len(matching) > 1:
                raise BusinessRuleError("ambiguous_requirement_tool_mapping")
            result_refs: list[str] = []
            if matching:
                definition = matching[0]
                status = {
                    "succeeded": "SATISFIED",
                    "student_failure": "NOT_SATISFIED",
                    "infrastructure_failure": "INFRASTRUCTURE_ERROR",
                }[result.status]
                requirement = await self.service.upsert_requirement_result(
                    session,
                    attempt=attempt,
                    requirement_id=definition.id,
                    snapshot_id=request.snapshot_id,
                    operation_id=request.operation_id,
                    status=status,
                    evaluator=f"openhands:{request.tool_name}:v1",
                    evidence_refs=list(evidence_refs),
                    version=definition.version,
                )
                result_refs.append(requirement.id)
            session.add(
                OperationLedger(
                    scope=scope,
                    operation_id=request.operation_id,
                    status="COMPLETED",
                    result_ref=result.output_ref,
                    result_payload={
                        "snapshot_id": request.snapshot_id,
                        "status": result.status,
                        "evidence_refs": list(evidence_refs),
                        "requirement_result_refs": result_refs,
                    },
                )
            )
            await session.commit()
            return evidence_refs

    def record(
        self, request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> tuple[str, ...]:
        return self.bridge.call(self._record(request, result))


def teaching_thread_id(attempt_id: str) -> str:
    return f"attempt:{attempt_id}:faq-001-v1"


class PersistentTeachingRuntime:
    """Runs the full graph with real business facts, checkpoints, and OpenHands."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        checkpoint_conninfo: str,
        executor: OpenHandsExecutor,
        llm: Any,
        service: BusinessService | None = None,
        tool_timeouts: dict[str, int] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.checkpoint_conninfo = checkpoint_conninfo
        self.executor = executor
        self.llm = llm
        self.service = service or BusinessService()
        self.tool_timeouts = dict(tool_timeouts or {})

    @staticmethod
    def config(attempt_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": teaching_thread_id(attempt_id)}}

    async def _initial_state(
        self, *, attempt_id: str, event: TeachingEvent
    ) -> TeachingState:
        async with self.session_factory() as session:
            attempt = await session.get(Attempt, attempt_id)
            if attempt is None:
                raise BusinessRuleError("attempt_not_found")
            version = await session.get(TaskVersion, attempt.task_version_id)
            task = await session.get(Task, version.task_id) if version else None
            if version is None or task is None or task.task_key != FAQ_TASK_KEY:
                raise BusinessRuleError("stage03_supports_only_faq_001_v1")
            stages = list(
                (
                    await session.scalars(
                        select(TaskStage)
                        .where(TaskStage.task_version_id == version.id)
                        .order_by(TaskStage.position)
                    )
                ).all()
            )
            current = next((item for item in stages if item.id == attempt.current_stage_id), None)
            if current is None:
                raise BusinessRuleError("current_stage_not_found")
            memberships = list(
                (
                    await session.scalars(
                        select(CourseMembership).where(
                            CourseMembership.course_id == task.course_id,
                            CourseMembership.role.in_(["teacher", "owner"]),
                        )
                    )
                ).all()
            )
            stored = dict(version.policy or {})
            policy = dict(DEFAULT_TASK_POLICY)
            policy.update({key: value for key, value in stored.items() if key not in {
                "allowed_tools", "tool_capabilities", "assessment_allowed_capabilities"
            }})
            policy["allowed_tools"] = list(DEFAULT_TASK_POLICY["allowed_tools"])
            policy["tool_capabilities"] = dict(DEFAULT_TASK_POLICY["tool_capabilities"])
            policy["assessment_allowed_capabilities"] = list(
                DEFAULT_TASK_POLICY["assessment_allowed_capabilities"]
            )
            policy["ai_guidance_enabled"] = not attempt.ai_guidance_paused
            state = new_teaching_state(
                attempt_id=attempt.id,
                task_version=f"{task.task_key}-{version.version}",
                learner_id=attempt.learner_id,
                incoming_event=event,
                authorized_teacher_ids=[item.user_id for item in memberships],
                mode=cast(Any, attempt.mode),
                state_version=attempt.state_version,
                policy_version=f"{task.task_key}-{version.version}-policy",
                teacher_policy=cast(TeachingPolicy, policy),
                stage_sequence=[item.stage_key for item in stages],
            )
            state["current_stage"] = current.stage_key
            state["stage_index"] = current.position
            state["student_failure_count"] = attempt.student_failure_count
            state["infrastructure_failure_count"] = attempt.infrastructure_failure_count
            return state

    def _dependencies(self, loop: asyncio.AbstractEventLoop) -> TeachingGraphDependencies:
        bridge = _LoopBridge(loop)
        return TeachingGraphDependencies(
            executor=self.executor,
            llm=self.llm,
            event_store=DatabaseTeachingEventStore(
                self.session_factory, self.service, bridge
            ),
            requirement_evaluator=DatabaseRequirementEvaluator(
                self.session_factory, self.service, bridge
            ),
            intervention_store=DatabaseInterventionStore(
                self.session_factory, self.service, bridge
            ),
            result_recorder=DatabaseToolResultRecorder(
                self.session_factory, self.service, bridge
            ),
            resource_policy=ResourcePolicy(),
            tool_timeouts={
                **{name: item.timeout_seconds for name, item in TOOL_CATALOG.items()},
                **self.tool_timeouts,
            },
        )

    async def _mark_interrupt(self, result: dict[str, Any], *, succeeded: bool) -> None:
        pending = result.get("pending_intervention")
        if not isinstance(pending, dict) or not pending.get("intervention_id"):
            return
        async with self.session_factory() as session:
            await self.service.mark_checkpoint_result(
                session,
                intervention_id=str(pending["intervention_id"]),
                succeeded=succeeded,
                error_code=None if succeeded else "checkpoint_write_failed",
            )

    async def run_event(
        self, *, attempt_id: str, event: TeachingEvent, automatic_followup: bool = True
    ) -> TeachingState:
        loop = asyncio.get_running_loop()
        dependencies = self._dependencies(loop)
        config = self.config(attempt_id)
        result: dict[str, Any] | None = None
        try:
            async with checkpoint_saver(self.checkpoint_conninfo) as saver:
                graph = build_teaching_graph(dependencies=dependencies, checkpointer=saver)
                checkpoint = await saver.aget(config)
                graph_input: dict[str, object]
                if checkpoint is None:
                    graph_input = dict(await self._initial_state(attempt_id=attempt_id, event=event))
                else:
                    graph_input = {"incoming_event": dict(event)}
                result = await graph.ainvoke(graph_input, config=config)
                if "__interrupt__" in result:
                    await self._mark_interrupt(result, succeeded=True)
                    return cast(TeachingState, result)
                if automatic_followup and result.get("last_tool_status") in {
                    "student_failure",
                    "infrastructure_failure",
                }:
                    followup: TeachingEvent = {
                        "event_id": str(uuid4()),
                        "event_type": "request_guidance",
                        "attempt_id": attempt_id,
                        "task_version": str(result["task_version"]),
                        "actor_id": str(result["learner_id"]),
                        "actor_role": "system",
                        "expected_state_version": int(result["state_version"]),
                        "operation_id": f"followup:{event['operation_id']}",
                        "snapshot_id": cast(str, result.get("latest_snapshot_id")),
                        "evidence_refs": list(result.get("evidence_refs", [])),
                        "evidence_summary": str(result.get("evidence_summary", "")),
                        "student_observation": str(
                            result.get("student_observation") or ""
                        ),
                        "failure_origin": "none",
                        "requested_guidance_kind": "question",
                    }
                    result = await graph.ainvoke(
                        {"incoming_event": followup}, config=config
                    )
                    if "__interrupt__" in result:
                        await self._mark_interrupt(result, succeeded=True)
                return cast(TeachingState, result)
        except Exception:
            operation_id = f"intervention:{event['operation_id']}"
            async with self.session_factory() as session:
                item = await session.scalar(
                    select(Intervention).where(Intervention.operation_id == operation_id)
                )
                if item is not None and item.status in {
                    INTERVENTION_CREATING,
                    INTERVENTION_CHECKPOINT_FAILED,
                }:
                    await self.service.mark_checkpoint_result(
                        session,
                        intervention_id=item.id,
                        succeeded=False,
                        error_code="checkpoint_write_failed",
                    )
            raise

    async def resume_intervention(
        self,
        *,
        intervention_id: str,
        teacher_id: str,
        resume_operation_id: str,
        expected_state_version: int,
        response: str,
        allow_l2: bool,
    ) -> Intervention:
        intervention, _ = await self.resume_intervention_with_state(
            intervention_id=intervention_id,
            teacher_id=teacher_id,
            resume_operation_id=resume_operation_id,
            expected_state_version=expected_state_version,
            response=response,
            allow_l2=allow_l2,
        )
        return intervention

    async def resume_intervention_with_state(
        self,
        *,
        intervention_id: str,
        teacher_id: str,
        resume_operation_id: str,
        expected_state_version: int,
        response: str,
        allow_l2: bool,
    ) -> tuple[Intervention, TeachingState]:
        async with self.session_factory() as session:
            intervention, duplicate = await self.service.begin_resume(
                session,
                intervention_id=intervention_id,
                teacher_id=teacher_id,
                resume_operation_id=resume_operation_id,
                expected_state_version=expected_state_version,
                response=response,
                allow_l2=allow_l2,
            )
            if duplicate and intervention.status == "RESOLVED":
                async with checkpoint_saver(self.checkpoint_conninfo) as saver:
                    graph = build_teaching_graph(
                        dependencies=self._dependencies(asyncio.get_running_loop()),
                        checkpointer=saver,
                    )
                    state = await graph.aget_state(self.config(intervention.attempt_id))
                return intervention, cast(TeachingState, state.values)
            attempt_id = intervention.attempt_id
        result: dict[str, Any]
        try:
            loop = asyncio.get_running_loop()
            async with checkpoint_saver(self.checkpoint_conninfo) as saver:
                graph = build_teaching_graph(
                    dependencies=self._dependencies(loop), checkpointer=saver
                )
                result = await graph.ainvoke(
                    Command(
                        resume={
                            "teacher_id": teacher_id,
                            "roles": ["teacher"],
                            "expected_state_version": expected_state_version,
                            "response": response,
                            "allow_l2": allow_l2,
                        }
                    ),
                    config=self.config(attempt_id),
                )
        except Exception as exc:
            async with self.session_factory() as session:
                await self.service.finish_resume(
                    session,
                    intervention_id=intervention_id,
                    resume_operation_id=resume_operation_id,
                    succeeded=False,
                    error_code=type(exc).__name__,
                )
            raise
        async with self.session_factory() as session:
            resolved = await self.service.finish_resume(
                session,
                intervention_id=intervention_id,
                resume_operation_id=resume_operation_id,
                succeeded=True,
            )
        return resolved, cast(TeachingState, result)
