"""Separate-process Stage 02B recovery probe; not a production worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from app.business.database import create_business_engine, create_session_factory
from app.business.models import Attempt, Course, CourseMembership, Intervention, User
from app.business.recovery import PostgresInterventionRecoveryRuntime
from app.business.service import BusinessRuleError, BusinessService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument(
        "action", choices={"seed", "start", "list", "resume", "recover", "show"}
    )
    result.add_argument("--attempt-id")
    result.add_argument("--intervention-id")
    result.add_argument("--teacher-id", default="teacher-02b")
    result.add_argument("--student-id", default="student-02b")
    result.add_argument("--operation-id")
    result.add_argument("--expected-state-version", type=int, default=0)
    result.add_argument("--response", default="请依据当前证据继续。")
    result.add_argument("--allow-l2", action="store_true")
    result.add_argument("--simulate-checkpoint-failure", action="store_true")
    result.add_argument("--simulate-resume-failure", action="store_true")
    return result


async def run(args: argparse.Namespace) -> dict[str, object]:
    database_url = os.environ["TEACHING_DATABASE_URL"]
    engine = create_business_engine(database_url)
    factory = create_session_factory(engine)
    service = BusinessService()
    try:
        if args.action == "seed":
            async with factory() as session:
                course = await session.get(Course, "course-02b")
                if course is None:
                    course = Course(id="course-02b", code="AI-APP-02B", name="02B恢复测试")
                    session.add_all([
                        course,
                        User(id=args.teacher_id, email="teacher-02b@example.test", display_name="02B教师", system_role="teacher"),
                        User(id=args.student_id, email="student-02b@example.test", display_name="02B学生", system_role="student"),
                    ])
                    await session.commit()
                    session.add_all([
                        CourseMembership(course_id=course.id, user_id=args.teacher_id, role="teacher"),
                        CourseMembership(course_id=course.id, user_id=args.student_id, role="student"),
                    ])
                    await session.commit()
                version = await service.create_faq_version(
                    session, course_id=course.id, actor_id=args.teacher_id
                )
                attempt = await service.create_attempt(
                    session,
                    task_version_id=version.id,
                    learner_id=args.student_id,
                    mode="guided_practice",
                )
                return {"attempt_id": attempt.id, "state_version": attempt.state_version}

        if args.action == "list":
            async with factory() as session:
                items = await service.list_waiting_interventions(session)
                visible = []
                for item in items:
                    attempt = await session.get(Attempt, item.attempt_id)
                    try:
                        await service.authorize_attempt(
                            session, attempt=attempt, user_id=args.teacher_id, teacher=True
                        )
                    except BusinessRuleError:
                        continue
                    visible.append(item.id)
                return {"waiting_intervention_ids": visible, "source": "teaching_business"}

        if args.action == "show":
            async with factory() as session:
                item = await session.get(Intervention, args.intervention_id)
                if item is None:
                    return {"status": "NOT_FOUND"}
                return {
                    "id": item.id,
                    "attempt_id": item.attempt_id,
                    "status": item.status,
                    "response": item.response,
                    "assigned_teacher_id": item.assigned_teacher_id,
                    "allow_l2": item.allow_l2,
                }

        waiting_source = None
        if args.action == "recover":
            async with factory() as session:
                waiting = await service.list_waiting_interventions(session)
                item = next(
                    (entry for entry in waiting if entry.id == args.intervention_id), None
                )
                if item is None:
                    raise BusinessRuleError("waiting_intervention_not_found")
                attempt = await session.get(Attempt, item.attempt_id)
                await service.authorize_attempt(
                    session, attempt=attempt, user_id=args.teacher_id, teacher=True
                )
                waiting_source = "teaching_business"

        checkpoint_url = os.environ["LANGGRAPH_CHECKPOINT_DATABASE_URL"]
        runtime = PostgresInterventionRecoveryRuntime(
            session_factory=factory,
            checkpoint_conninfo=checkpoint_url,
            service=service,
        )
        if args.action == "start":
            item = await runtime.start(
                attempt_id=args.attempt_id,
                operation_id=args.operation_id,
                reason="failure_threshold_reached",
                evidence_refs=["evidence://stage02b/process-a"],
                simulate_checkpoint_failure=args.simulate_checkpoint_failure,
            )
        else:
            item = await runtime.resume(
                intervention_id=args.intervention_id,
                teacher_id=args.teacher_id,
                resume_operation_id=args.operation_id,
                expected_state_version=args.expected_state_version,
                response=args.response,
                allow_l2=args.allow_l2,
                simulate_resume_failure=args.simulate_resume_failure,
            )
        return {
            "intervention_id": item.id,
            "status": item.status,
            "waiting_source": waiting_source,
        }
    finally:
        await engine.dispose()


async def main() -> None:
    args = parser().parse_args()
    print(json.dumps(await run(args), ensure_ascii=False))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
