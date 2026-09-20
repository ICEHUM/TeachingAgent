from __future__ import annotations

from typing import TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .checkpoint import checkpoint_saver
from .models import Attempt, Intervention
from .service import (
    INTERVENTION_CHECKPOINT_FAILED,
    INTERVENTION_CREATING,
    BusinessRuleError,
    BusinessService,
)


class InterventionRecoveryState(TypedDict, total=False):
    attempt_id: str
    intervention_id: str
    state_version: int
    reason: str
    recovery_status: str
    resumed_by: str
    allow_l2: bool


def _teacher_gate(state: InterventionRecoveryState) -> dict[str, object]:
    resumed = interrupt(
        {
            "attempt_id": state["attempt_id"],
            "intervention_id": state["intervention_id"],
            "state_version": state["state_version"],
            "reason": state["reason"],
        }
    )
    if not isinstance(resumed, dict):
        raise BusinessRuleError("invalid_resume_payload")
    return {
        "recovery_status": "RESUMED",
        "resumed_by": str(resumed.get("teacher_id", "")),
        "allow_l2": bool(resumed.get("allow_l2", False)),
    }


def build_intervention_recovery_graph(checkpointer):
    """Small durable gate used to prove process-independent interrupt recovery."""

    builder = StateGraph(InterventionRecoveryState)
    builder.add_node("teacher_gate", _teacher_gate)
    builder.add_edge(START, "teacher_gate")
    builder.add_edge("teacher_gate", END)
    return builder.compile(checkpointer=checkpointer)


def recovery_thread_id(intervention: Intervention) -> str:
    return f"attempt:{intervention.attempt_id}:intervention:{intervention.id}"


class PostgresInterventionRecoveryRuntime:
    """Coordinates business facts with a separately pooled PostgreSQL checkpointer."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        checkpoint_conninfo: str,
        service: BusinessService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.checkpoint_conninfo = checkpoint_conninfo
        self.service = service or BusinessService()

    @staticmethod
    def _config(intervention: Intervention) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": recovery_thread_id(intervention)}}

    async def start(
        self,
        *,
        attempt_id: str,
        operation_id: str,
        reason: str,
        evidence_refs: list[str],
        simulate_checkpoint_failure: bool = False,
    ) -> Intervention:
        async with self.session_factory() as session:
            attempt = await session.get(Attempt, attempt_id)
            if attempt is None:
                raise BusinessRuleError("attempt_not_found")
            intervention, duplicate = await self.service.create_intervention(
                session,
                attempt=attempt,
                operation_id=operation_id,
                reason=reason,
                evidence_refs=evidence_refs,
            )
            if duplicate and intervention.status == INTERVENTION_CHECKPOINT_FAILED:
                intervention = await self.service.prepare_checkpoint_retry(
                    session, intervention_id=intervention.id
                )
            if intervention.status != INTERVENTION_CREATING:
                return intervention
            intervention_id = intervention.id
            initial: InterventionRecoveryState = {
                "attempt_id": attempt.id,
                "intervention_id": intervention.id,
                "state_version": intervention.requested_state_version,
                "reason": intervention.reason,
                "recovery_status": "WAITING_FOR_CHECKPOINT",
            }
            config = self._config(intervention)

        try:
            if simulate_checkpoint_failure:
                raise RuntimeError("simulated_checkpoint_failure")
            async with checkpoint_saver(self.checkpoint_conninfo) as saver:
                graph = build_intervention_recovery_graph(saver)
                result = await graph.ainvoke(initial, config=config)
            if "__interrupt__" not in result:
                raise RuntimeError("interrupt_checkpoint_missing")
        except Exception as exc:
            async with self.session_factory() as session:
                await self.service.mark_checkpoint_result(
                    session,
                    intervention_id=intervention_id,
                    succeeded=False,
                    error_code=type(exc).__name__,
                )
            raise

        async with self.session_factory() as session:
            return await self.service.mark_checkpoint_result(
                session, intervention_id=intervention_id, succeeded=True
            )

    async def resume(
        self,
        *,
        intervention_id: str,
        teacher_id: str,
        resume_operation_id: str,
        expected_state_version: int,
        response: str,
        allow_l2: bool,
        simulate_resume_failure: bool = False,
    ) -> Intervention:
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
                return intervention
            config = self._config(intervention)

        try:
            if simulate_resume_failure:
                raise RuntimeError("simulated_resume_failure")
            resume_payload = {
                "teacher_id": teacher_id,
                "expected_state_version": expected_state_version,
                "response": response,
                "allow_l2": allow_l2,
            }
            async with checkpoint_saver(self.checkpoint_conninfo) as saver:
                graph = build_intervention_recovery_graph(saver)
                result = await graph.ainvoke(Command(resume=resume_payload), config=config)
            recovered = cast(InterventionRecoveryState, result)
            if recovered.get("recovery_status") != "RESUMED":
                raise RuntimeError("resume_checkpoint_missing")
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
            return await self.service.finish_resume(
                session,
                intervention_id=intervention_id,
                resume_operation_id=resume_operation_id,
                succeeded=True,
            )
