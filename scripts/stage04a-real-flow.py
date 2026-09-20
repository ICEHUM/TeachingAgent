"""Real Stage 04A success, teacher-resume, and DeepSeek validation."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select

from app.agent.teaching_llm import DeepSeekTeachingLLM
from app.agent.tools import OpenHandsExecutor
from app.agent.workspace import AttemptWorkspaceManager
from app.business.database import create_business_engine, create_session_factory
from app.business.models import (
    Attempt,
    Course,
    CourseMembership,
    Intervention,
    RequirementDefinition,
    RequirementResult,
    Snapshot,
    TaskStage,
    TeachingEvent,
    User,
)
from app.business.service import BusinessService
from app.business.stage03 import PersistentTeachingRuntime
from app.teaching_control.protocols import GuidanceRequest


def environment() -> tuple[str, str]:
    business = os.environ.get("TEACHING_DATABASE_URL", "")
    checkpoint = os.environ.get("CHECKPOINT_DATABASE_URL", "")
    if not business or not checkpoint:
        raise RuntimeError("TEACHING_DATABASE_URL and CHECKPOINT_DATABASE_URL are required")
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    return business, checkpoint


def sources() -> list[dict[str, str]]:
    return [
        {
            "question": "如何重置密码？",
            "answer": "在账户安全页选择重置密码。",
            "source": "faq://account/reset-password",
        },
        {
            "question": "如何联系人工客服？",
            "answer": "在帮助中心提交工单。",
            "source": "faq://support/ticket",
        },
        {
            "question": "退款需要多久？",
            "answer": "审核通过后五个工作日内到账。",
            "source": "faq://billing/refund",
        },
    ]


def write_project(directory: Path, *, fixed: bool) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data").mkdir(exist_ok=True)
    (directory / "data" / "faq.json").write_text(
        json.dumps(sources(), ensure_ascii=False), encoding="utf-8"
    )
    retrieve = (
        "    return [item for item in sources if item.get('question', '').strip() == question.strip()]\n"
        if fixed
        else "    return []\n"
    )
    (directory / "faq_app.py").write_text(
        "import json\n"
        "from pathlib import Path\n\n"
        "def load_sources(path):\n"
        "    return json.loads(Path(path).read_text(encoding='utf-8'))\n\n"
        "def retrieve(question, sources):\n"
        + retrieve,
        encoding="utf-8",
    )


def run_event(
    *,
    attempt_id: str,
    learner_id: str,
    state_version: int,
    snapshot_id: str,
    operation_id: str,
    observation: str = "",
) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "event_type": "run_tool",
        "attempt_id": attempt_id,
        "task_version": "FAQ-001-v1",
        "actor_id": learner_id,
        "actor_role": "student",
        "expected_state_version": state_version,
        "operation_id": operation_id,
        "snapshot_id": snapshot_id,
        "evidence_refs": [],
        "evidence_summary": "",
        "student_observation": observation,
        "failure_origin": "none",
        "requested_guidance_kind": "question",
        "requested_tool": "run_faq_tests",
    }


async def seed_attempt(factory, service, manager, label: str) -> dict[str, str]:
    suffix = uuid4().hex[:10]
    async with factory() as session:
        teacher = User(
            email=f"stage04-{label}-teacher-{suffix}@example.test",
            display_name="Stage04 教师",
            system_role="teacher",
        )
        learner = User(
            email=f"stage04-{label}-student-{suffix}@example.test",
            display_name="Stage04 学生",
            system_role="student",
        )
        course = Course(code=f"STAGE04-{label}-{suffix}", name="AI应用开发实训 Stage04")
        session.add_all([teacher, learner, course])
        await session.flush()
        session.add_all(
            [
                CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
                CourseMembership(course_id=course.id, user_id=learner.id, role="student"),
            ]
        )
        await session.commit()
        version = await service.create_faq_version(
            session, course_id=course.id, actor_id=teacher.id
        )
        attempt = await service.create_attempt(
            session,
            task_version_id=version.id,
            learner_id=learner.id,
            mode="guided_practice",
        )
        stage = await session.scalar(
            select(TaskStage).where(
                TaskStage.task_version_id == version.id,
                TaskStage.stage_key == "implement_retrieval",
            )
        )
        if stage is None:
            raise RuntimeError("implement_retrieval stage missing")
        attempt.current_stage_id = stage.id
        await session.commit()
        source = manager.source_directory(attempt.id)
        write_project(source, fixed=False)
        snapshot_a = str(uuid4())
        _, snapshot_ref = manager.create_snapshot(
            attempt_id=attempt.id, snapshot_id=snapshot_a
        )
        await service.register_snapshot(
            session,
            attempt=attempt,
            snapshot_id=snapshot_a,
            snapshot_ref=snapshot_ref,
            sequence=1,
        )
        return {
            "attempt_id": attempt.id,
            "learner_id": learner.id,
            "teacher_id": teacher.id,
            "snapshot_a": snapshot_a,
        }


def guidance_view(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    return {
        key: value.get(key)
        for key in (
            "level",
            "message",
            "next_step",
            "uncertainty",
            "provider",
            "model",
            "request_id",
            "latency_ms",
            "prompt_tokens",
            "completion_tokens",
            "success",
            "fallback_reason",
            "evidence_refs",
            "raw_output_ref",
            "audit_ref",
        )
    }


async def result_rows(factory, attempt_id: str) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(RequirementResult, RequirementDefinition.requirement_key)
                    .join(
                        RequirementDefinition,
                        RequirementDefinition.id == RequirementResult.requirement_id,
                    )
                    .where(RequirementResult.attempt_id == attempt_id)
                    .order_by(RequirementResult.evaluated_at, RequirementResult.id)
                )
            ).all()
        )
    return [
        {
            "id": item.id,
            "requirement_key": key,
            "snapshot_id": item.snapshot_id,
            "operation_id": item.operation_id,
            "status": item.status,
            "evaluator": item.evaluator,
            "evidence_refs": item.evidence_refs,
            "evaluated_at": item.evaluated_at.isoformat(),
            "version": item.version,
        }
        for item, key in rows
    ]


async def timeline(factory, attempt_id: str) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(TeachingEvent)
                    .where(TeachingEvent.attempt_id == attempt_id)
                    .order_by(TeachingEvent.state_version, TeachingEvent.created_at)
                )
            ).all()
        )
    return [
        {
            "state_version": row.state_version,
            "event_type": row.event_type,
            "operation_id": row.operation_id,
            "decision": row.payload.get("decision"),
            "stage": row.payload.get("stage"),
            "snapshot_id": row.payload.get("snapshot_id"),
            "help_level": row.payload.get("help_level"),
            "check_status": row.payload.get("check_status"),
        }
        for row in rows
    ]


async def main(report_path: Path) -> None:
    business_url, checkpoint_url = environment()
    engine = create_business_engine(business_url)
    factory = create_session_factory(engine)
    service = BusinessService()
    manager = AttemptWorkspaceManager()
    llm = DeepSeekTeachingLLM(timeout_seconds=30)
    runtime = PersistentTeachingRuntime(
        session_factory=factory,
        checkpoint_conninfo=checkpoint_url,
        executor=OpenHandsExecutor(manager),
        llm=llm,
    )

    success_ids = await seed_attempt(factory, service, manager, "success")
    first = await runtime.run_event(
        attempt_id=success_ids["attempt_id"],
        event=run_event(
            attempt_id=success_ids["attempt_id"],
            learner_id=success_ids["learner_id"],
            state_version=0,
            snapshot_id=success_ids["snapshot_a"],
            operation_id=f"stage04-first-{uuid4().hex[:8]}",
        ),
    )
    second = await runtime.run_event(
        attempt_id=success_ids["attempt_id"],
        event=run_event(
            attempt_id=success_ids["attempt_id"],
            learner_id=success_ids["learner_id"],
            state_version=first["state_version"],
            snapshot_id=success_ids["snapshot_a"],
            operation_id=f"stage04-second-{uuid4().hex[:8]}",
            observation="资料加载正常，已知问题仍返回空列表，我准备检查查询文本与问题字段的匹配条件。",
        ),
    )
    if first["help_level"] != "L0" or second["help_level"] != "L1":
        raise RuntimeError("real DeepSeek flow did not preserve server-selected L0/L1")
    if not first["guidance"]["success"] or not second["guidance"]["success"]:
        raise RuntimeError("real DeepSeek L0/L1 output fell back unexpectedly")

    # Explicit student edit fixture: no model or OpenHands tool writes student source.
    write_project(manager.source_directory(success_ids["attempt_id"]), fixed=True)
    snapshot_b = str(uuid4())
    _, snapshot_b_ref = manager.create_snapshot(
        attempt_id=success_ids["attempt_id"], snapshot_id=snapshot_b
    )
    async with factory() as session:
        success_attempt = await session.get(Attempt, success_ids["attempt_id"])
        await service.register_snapshot(
            session,
            attempt=success_attempt,
            snapshot_id=snapshot_b,
            snapshot_ref=snapshot_b_ref,
            sequence=2,
        )
    passed = await runtime.run_event(
        attempt_id=success_ids["attempt_id"],
        event=run_event(
            attempt_id=success_ids["attempt_id"],
            learner_id=success_ids["learner_id"],
            state_version=second["state_version"],
            snapshot_id=snapshot_b,
            operation_id=f"stage04-passed-{uuid4().hex[:8]}",
            observation="我自行修改了检索匹配条件，并用已知问题命中、未知问题空结果进行了复验。",
        ),
    )
    async with factory() as session:
        success_attempt = await session.get(Attempt, success_ids["attempt_id"])
        current_stage = await session.get(TaskStage, success_attempt.current_stage_id)
        snapshot_rows = list(
            (
                await session.scalars(
                    select(Snapshot).where(Snapshot.attempt_id == success_attempt.id)
                )
            ).all()
        )
    results = await result_rows(factory, success_ids["attempt_id"])
    a_rows = [row for row in results if row["snapshot_id"] == success_ids["snapshot_a"]]
    b_rows = [row for row in results if row["snapshot_id"] == snapshot_b]
    if current_stage.stage_key != "generate_cited_answer":
        raise RuntimeError("server requirement aggregation did not advance the stage")
    if {row["requirement_key"] for row in b_rows if row["status"] == "SATISFIED"} != {
        "retrieval_public_tests",
        "retrieval_observation",
    }:
        raise RuntimeError("Snapshot B did not satisfy both required requirements")
    if not any(row["status"] == "NOT_SATISFIED" for row in a_rows):
        raise RuntimeError("Snapshot A failure evidence was not retained")
    if len(snapshot_rows) != 2 or passed["flow_status"] != "READY_FOR_NEXT_STAGE":
        raise RuntimeError("snapshot or graph stage transition contract failed")

    teacher_ids = await seed_attempt(factory, service, manager, "resume")
    teacher_first = await runtime.run_event(
        attempt_id=teacher_ids["attempt_id"],
        event=run_event(
            attempt_id=teacher_ids["attempt_id"],
            learner_id=teacher_ids["learner_id"],
            state_version=0,
            snapshot_id=teacher_ids["snapshot_a"],
            operation_id=f"stage04-resume-first-{uuid4().hex[:8]}",
        ),
    )
    teacher_second = await runtime.run_event(
        attempt_id=teacher_ids["attempt_id"],
        event=run_event(
            attempt_id=teacher_ids["attempt_id"],
            learner_id=teacher_ids["learner_id"],
            state_version=teacher_first["state_version"],
            snapshot_id=teacher_ids["snapshot_a"],
            operation_id=f"stage04-resume-second-{uuid4().hex[:8]}",
            observation="资料加载正常但检索为空，我已检查输入并准备进一步定位匹配条件。",
        ),
    )
    teacher_third = await runtime.run_event(
        attempt_id=teacher_ids["attempt_id"],
        event=run_event(
            attempt_id=teacher_ids["attempt_id"],
            learner_id=teacher_ids["learner_id"],
            state_version=teacher_second["state_version"],
            snapshot_id=teacher_ids["snapshot_a"],
            operation_id=f"stage04-resume-third-{uuid4().hex[:8]}",
            observation="第三次复验仍为空，我已保留输入、资料加载结果和检索返回值。",
        ),
    )
    if "__interrupt__" not in teacher_third:
        raise RuntimeError("third failure did not reach teacher interrupt")
    async with factory() as session:
        intervention = await session.scalar(
            select(Intervention).where(
                Intervention.attempt_id == teacher_ids["attempt_id"],
                Intervention.status == "WAITING_TEACHER",
            )
        )
    if intervention is None:
        raise RuntimeError("WAITING_TEACHER intervention missing")
    resolved, resumed_state = await runtime.resume_intervention_with_state(
        intervention_id=intervention.id,
        teacher_id=teacher_ids["teacher_id"],
        resume_operation_id=f"stage04-teacher-resume-{uuid4().hex[:8]}",
        expected_state_version=intervention.requested_state_version,
        response="允许一次L2局部方向提示，学生仍需自行完成修改。",
        allow_l2=True,
    )
    if resolved.status != "RESOLVED":
        raise RuntimeError("teacher resume did not resolve intervention")
    if resumed_state["guidance"]["level"] != "L2":
        raise RuntimeError("teacher-authorized resume did not continue with L2 guidance")

    assessment_ref = tuple(first["guidance"]["evidence_refs"])
    assessment = llm.generate_guidance(
        GuidanceRequest(
            operation_id=f"guidance:stage04-assessment-{uuid4().hex[:8]}",
            attempt_id=success_ids["attempt_id"],
            task_version="FAQ-001-v1",
            stage="implement_retrieval",
            mode="assessment",
            level="L0",
            kind="question",
            evidence_refs=assessment_ref,
            evidence_summary="已知问题检索返回空列表。",
        )
    )
    if not assessment.success or assessment.level != "L0":
        raise RuntimeError("real assessment TeachingLLM call did not remain at L0")

    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "provider": first["guidance"]["provider"],
        "model": first["guidance"]["model"],
        "success_flow": {
            "attempt_id": success_ids["attempt_id"],
            "snapshot_a": success_ids["snapshot_a"],
            "snapshot_b": snapshot_b,
            "l0": guidance_view(first["guidance"]),
            "l1": guidance_view(second["guidance"]),
            "result_rows": results,
            "stage_requirements_met": passed["stage_requirements_met"],
            "stage_assessment": passed["stage_assessment"],
            "advanced_to": current_stage.stage_key,
            "flow_status": passed["flow_status"],
            "timeline": await timeline(factory, success_ids["attempt_id"]),
        },
        "teacher_resume": {
            "attempt_id": teacher_ids["attempt_id"],
            "intervention_id": intervention.id,
            "before": "WAITING_TEACHER",
            "after": resolved.status,
            "allow_l2": resolved.allow_l2,
            "resume_state_version": resumed_state["state_version"],
            "guidance": guidance_view(resumed_state["guidance"]),
            "timeline": await timeline(factory, teacher_ids["attempt_id"]),
        },
        "assessment": {
            "level": assessment.level,
            "message": assessment.message,
            "next_step": assessment.next_step,
            "success": assessment.success,
            "fallback_reason": assessment.fallback_reason,
            "evidence_refs": list(assessment.evidence_refs),
            "raw_output_ref": assessment.raw_output_ref,
            "audit_ref": assessment.audit_ref,
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "reports/stage04a-real-verification.json"
    asyncio.run(main(target))
