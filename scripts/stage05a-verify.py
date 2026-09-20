"""Verify the persisted Stage 05A browser journey without exposing credentials."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.business.database import create_business_engine, create_session_factory
from app.business.models import (
    Attempt,
    Intervention,
    RequirementDefinition,
    RequirementResult,
    Snapshot,
    TaskStage,
    TeachingEvent,
)


def compact_event(event: TeachingEvent) -> dict[str, object]:
    payload = event.payload or {}
    guidance = payload.get("guidance") if isinstance(payload.get("guidance"), dict) else {}
    return {
        "time": event.created_at.isoformat(),
        "event_type": event.event_type,
        "state_version": event.state_version,
        "stage": payload.get("stage") or payload.get("from_stage"),
        "to_stage": payload.get("to_stage"),
        "decision": payload.get("decision"),
        "snapshot_id": payload.get("snapshot_id"),
        "check_status": payload.get("check_status"),
        "guidance_level": guidance.get("level"),
        "guidance_success": guidance.get("success"),
        "guidance_fallback_reason": guidance.get("fallback_reason"),
        "evidence_count": len(payload.get("evidence_refs") or []),
    }


async def main() -> None:
    database_url = os.environ.get("TEACHING_DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("TEACHING_DATABASE_URL is required")
    context = json.loads((ROOT / ".runtime" / "stage05a-context.json").read_text(encoding="utf-8"))
    engine = create_business_engine(database_url)
    factory = create_session_factory(engine)
    async with factory() as session:
        student_attempt = await session.get(Attempt, context["student"]["attempt_id"])
        teacher_attempt = await session.get(Attempt, context["teacher_case"]["attempt_id"])
        intervention = await session.get(Intervention, context["teacher_case"]["intervention_id"])
        if not student_attempt or not teacher_attempt or not intervention:
            raise RuntimeError("Stage 05A facts are incomplete")

        current_stage = await session.get(TaskStage, student_attempt.current_stage_id)
        implementation_stage = await session.scalar(
            select(TaskStage).where(
                TaskStage.task_version_id == student_attempt.task_version_id,
                TaskStage.stage_key == "implement_retrieval",
            )
        )
        snapshots = list((await session.scalars(
            select(Snapshot).where(Snapshot.attempt_id == student_attempt.id).order_by(Snapshot.sequence)
        )).all())
        definitions = list((await session.scalars(
            select(RequirementDefinition)
            .where(RequirementDefinition.task_stage_id == implementation_stage.id)
            .order_by(RequirementDefinition.requirement_key)
        )).all())
        definition_by_id = {item.id: item for item in definitions}
        results = list((await session.scalars(
            select(RequirementResult)
            .where(RequirementResult.attempt_id == student_attempt.id)
            .order_by(RequirementResult.evaluated_at)
        )).all())
        student_events = list((await session.scalars(
            select(TeachingEvent)
            .where(TeachingEvent.attempt_id == student_attempt.id)
            .order_by(TeachingEvent.created_at)
        )).all())
        teacher_events = list((await session.scalars(
            select(TeachingEvent)
            .where(TeachingEvent.attempt_id == teacher_attempt.id)
            .order_by(TeachingEvent.created_at)
        )).all())

        snapshot_sequences = {item.id: item.sequence for item in snapshots}
        result_history = [
            {
                "requirement": definition_by_id[item.requirement_id].requirement_key,
                "kind": definition_by_id[item.requirement_id].kind,
                "snapshot": snapshot_sequences.get(item.snapshot_id),
                "status": item.status,
                "evaluator": item.evaluator,
                "operation_id": item.operation_id,
                "evidence_count": len(item.evidence_refs or []),
                "evaluated_at": item.evaluated_at.isoformat(),
            }
            for item in results
            if item.requirement_id in definition_by_id
        ]

        latest_sequence = snapshots[-1].sequence
        latest_status = {
            item["requirement"]: item["status"]
            for item in result_history
            if item["snapshot"] == latest_sequence
        }
        required_keys = {item.requirement_key for item in definitions if item.required}
        assert len(snapshots) >= 2, "Snapshot B missing"
        assert context["student"]["snapshot_a"] == snapshots[0].id, "Snapshot A identity changed"
        assert current_stage and current_stage.stage_key == "generate_cited_answer", "stage did not advance"
        assert required_keys <= latest_status.keys(), "Snapshot B lacks required results"
        assert all(latest_status[key] == "SATISFIED" for key in required_keys), "Snapshot B is not satisfied"
        assert any(item.event_type == "stage_advanced" for item in student_events), "advance event missing"
        assert intervention.status == "RESOLVED", "teacher intervention was not resumed"
        assert intervention.allow_l2 is True, "teacher did not authorize L2"
        assert any(
            isinstance((item.payload or {}).get("guidance"), dict)
            and (item.payload or {})["guidance"].get("level") == "L2"
            for item in teacher_events
        ), "post-resume L2 guidance missing"

        report = {
            "student_success": {
                "attempt_id": student_attempt.id,
                "snapshot_a": snapshots[0].id,
                "snapshot_b": snapshots[-1].id,
                "snapshot_count": len(snapshots),
                "current_stage": current_stage.stage_key,
                "state_version": student_attempt.state_version,
                "required_latest_status": {key: latest_status[key] for key in sorted(required_keys)},
                "requirement_result_history": result_history,
                "timeline": [compact_event(item) for item in student_events],
            },
            "teacher_resume": {
                "attempt_id": teacher_attempt.id,
                "intervention_id": intervention.id,
                "status": intervention.status,
                "allow_l2": intervention.allow_l2,
                "response_present": bool(intervention.response),
                "state_version": teacher_attempt.state_version,
                "timeline": [compact_event(item) for item in teacher_events],
            },
            "assertions": {
                "browser_created_snapshot_b": "PASS",
                "snapshot_b_only_acceptance": "PASS",
                "server_aggregated_stage_advance": "PASS",
                "teacher_resume_completed": "PASS",
                "post_resume_l2_guidance": "PASS",
            },
        }
    output = ROOT / "reports" / "stage05a-e2e-verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["assertions"], ensure_ascii=False))
    await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
