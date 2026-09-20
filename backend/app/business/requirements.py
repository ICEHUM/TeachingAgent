from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Attempt, RequirementDefinition, RequirementResult, Snapshot, TaskStage


@dataclass(frozen=True, slots=True)
class StageRequirementAggregate:
    satisfied: bool
    required_count: int
    satisfied_count: int
    result_refs: tuple[str, ...]


class RequirementEvaluator:
    """Aggregates versioned server-owned results; request payload flags are ignored."""

    async def evaluate(self, session: AsyncSession, *, attempt_id: str) -> StageRequirementAggregate:
        attempt = await session.get(Attempt, attempt_id)
        if attempt is None:
            raise LookupError("attempt_not_found")
        definitions = list((await session.scalars(
            select(RequirementDefinition)
            .join(TaskStage, RequirementDefinition.task_stage_id == TaskStage.id)
            .where(TaskStage.id == attempt.current_stage_id, RequirementDefinition.required.is_(True))
        )).all())
        if not definitions:
            return StageRequirementAggregate(False, 0, 0, ())
        snapshot = await session.scalar(
            select(Snapshot)
            .where(Snapshot.attempt_id == attempt_id)
            .order_by(Snapshot.sequence.desc())
            .limit(1)
        )
        if snapshot is None:
            return StageRequirementAggregate(False, len(definitions), 0, ())
        definition_ids = [item.id for item in definitions]
        results = list((await session.scalars(
            select(RequirementResult).where(
                RequirementResult.attempt_id == attempt_id,
                RequirementResult.requirement_id.in_(definition_ids),
                RequirementResult.snapshot_id == snapshot.id,
            ).order_by(RequirementResult.evaluated_at, RequirementResult.id)
        )).all())
        latest = {result.requirement_id: result for result in results}
        passed = {
            requirement_id: result
            for requirement_id, result in latest.items()
            if result.status == "SATISFIED"
        }
        return StageRequirementAggregate(
            satisfied=len(passed) == len(definitions),
            required_count=len(definitions),
            satisfied_count=len(passed),
            result_refs=tuple(result.id for result in passed.values()),
        )
