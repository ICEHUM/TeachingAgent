"""Two-process real PostgreSQL, LangGraph, OpenHands, and workspace verification."""

from __future__ import annotations

import argparse
import asyncio
import base64
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

from app.agent.tools import STUDENT_EXEC_PREFIX, OpenHandsExecutor
from app.agent.workspace import (
    AttemptWorkspaceManager,
    attempt_snapshot_workspace,
)
from app.business.checkpoint import checkpoint_saver
from app.business.database import create_business_engine, create_session_factory
from app.business.models import (
    Attempt,
    Course,
    CourseMembership,
    Intervention,
    OperationLedger,
    RequirementResult,
    Snapshot,
    TaskStage,
    TeachingEvent,
    User,
)
from app.business.service import BusinessService
from app.business.stage03 import PersistentTeachingRuntime
from app.teaching_control.fakes import FakeTeachingLLM
from app.teaching_control.protocols import ResourcePolicy, ToolExecutionRequest


def environment() -> tuple[str, str]:
    business = os.environ.get("TEACHING_DATABASE_URL", "")
    checkpoint = os.environ.get("CHECKPOINT_DATABASE_URL", "")
    if not business or not checkpoint:
        raise RuntimeError("TEACHING_DATABASE_URL and CHECKPOINT_DATABASE_URL are required")
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    return business, checkpoint


def write_broken_project(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data").mkdir(exist_ok=True)
    sources = [
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
    (directory / "data" / "faq.json").write_text(
        json.dumps(sources, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "faq_app.py").write_text(
        "import json\n"
        "from pathlib import Path\n\n"
        "def load_sources(path):\n"
        "    return json.loads(Path(path).read_text(encoding='utf-8'))\n\n"
        "def retrieve(question, sources):\n"
        "    # Controlled student defect: valid sources are loaded but no hit is returned.\n"
        "    return []\n",
        encoding="utf-8",
    )


def event(
    *,
    attempt_id: str,
    learner_id: str,
    state_version: int,
    snapshot_id: str,
    operation_id: str,
    observation: str = "",
    tool_name: str = "run_faq_tests",
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
        "requested_tool": tool_name,
    }


async def seed(context_path: Path) -> None:
    business_url, checkpoint_url = environment()
    engine = create_business_engine(business_url)
    factory = create_session_factory(engine)
    service = BusinessService()
    suffix = uuid4().hex[:10]
    async with factory() as session:
        teacher = User(
            email=f"stage03-teacher-{suffix}@example.test",
            display_name="Stage03 教师",
            system_role="teacher",
        )
        learner = User(
            email=f"stage03-student-{suffix}@example.test",
            display_name="Stage03 学生",
            system_role="student",
        )
        course = Course(code=f"STAGE03-{suffix}", name="AI应用开发实训 Stage03")
        session.add_all([teacher, learner, course])
        await session.flush()
        session.add_all(
            [
                CourseMembership(
                    course_id=course.id, user_id=teacher.id, role="teacher"
                ),
                CourseMembership(
                    course_id=course.id, user_id=learner.id, role="student"
                ),
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
        retrieval_stage = await session.scalar(
            select(TaskStage).where(
                TaskStage.task_version_id == version.id,
                TaskStage.stage_key == "implement_retrieval",
            )
        )
        attempt.current_stage_id = retrieval_stage.id
        await session.commit()

        manager = AttemptWorkspaceManager()
        write_broken_project(manager.source_directory(attempt.id))
        snapshot_id = str(uuid4())
        _, snapshot_ref = manager.create_snapshot(
            attempt_id=attempt.id, snapshot_id=snapshot_id
        )
        await service.register_snapshot(
            session,
            attempt=attempt,
            snapshot_id=snapshot_id,
            snapshot_ref=snapshot_ref,
            sequence=1,
        )

        # A second student's secret exists on the host but is never mounted in A's container.
        other_attempt_id = str(uuid4())
        other_source = manager.source_directory(other_attempt_id)
        other_source.joinpath("private-b.txt").write_text(
            "B-STUDENT-PRIVATE", encoding="utf-8"
        )
        other_snapshot = str(uuid4())
        manager.create_snapshot(
            attempt_id=other_attempt_id, snapshot_id=other_snapshot
        )

    os.environ["LLM_API_KEY"] = "STAGE03-HOST-SECRET-MUST-NOT-ENTER-CONTAINER"
    probe_source = """
import os
import socket
assert os.getuid() == 65534
assert not os.path.exists('/var/run/docker.sock')
assert os.environ.get('LLM_API_KEY') is None
assert os.environ.get('OH_SESSION_API_KEYS_0') is None
assert not os.path.exists('/workspace/student/private-b.txt')
try:
    open('/proc/1/environ', 'rb').read(1)
except OSError:
    pass
else:
    raise SystemExit('agent process environment unexpectedly readable')
socket.setdefaulttimeout(2)
try:
    socket.create_connection(('1.1.1.1', 53), timeout=2)
except OSError:
    print('security_probe=passed')
else:
    raise SystemExit('external network unexpectedly reachable')
"""
    encoded_probe = base64.b64encode(probe_source.encode("utf-8")).decode("ascii")
    with attempt_snapshot_workspace(
        manager=manager, attempt_id=attempt.id, snapshot_id=snapshot_id
    ) as running:
        probe = running.workspace.execute_command(
            f"{STUDENT_EXEC_PREFIX} python -I -c "
            f"\"import base64;exec(base64.b64decode('{encoded_probe}'))\"",
            cwd="/workspace/student",
            timeout=10,
        )
        if probe.exit_code != 0 or "security_probe=passed" not in probe.stdout:
            raise RuntimeError(f"workspace security probe failed: {probe.stderr}")

    runtime = PersistentTeachingRuntime(
        session_factory=factory,
        checkpoint_conninfo=checkpoint_url,
        executor=OpenHandsExecutor(manager),
        llm=FakeTeachingLLM(),
    )
    first = await runtime.run_event(
        attempt_id=attempt.id,
        event=event(
            attempt_id=attempt.id,
            learner_id=learner.id,
            state_version=0,
            snapshot_id=snapshot_id,
            operation_id=f"stage03-first-{suffix}",
        ),
    )
    if first["help_level"] != "L0" or first["student_failure_count"] != 1:
        raise RuntimeError("first real failure did not produce L0")
    context = {
        "process_a_pid": os.getpid(),
        "attempt_id": attempt.id,
        "learner_id": learner.id,
        "teacher_id": teacher.id,
        "snapshot_id": snapshot_id,
        "other_attempt_id": other_attempt_id,
        "state_version": first["state_version"],
        "first_guidance": first["guidance"],
        "first_evidence_refs": first["evidence_refs"],
        "security_probe": "passed",
    }
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"phase": "process_a", **context}, ensure_ascii=False))
    await engine.dispose()


async def continue_flow(context_path: Path, report_path: Path) -> None:
    business_url, checkpoint_url = environment()
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if context["process_a_pid"] == os.getpid():
        raise RuntimeError("process-level recovery requires a new process")
    engine = create_business_engine(business_url)
    factory = create_session_factory(engine)
    manager = AttemptWorkspaceManager()
    runtime = PersistentTeachingRuntime(
        session_factory=factory,
        checkpoint_conninfo=checkpoint_url,
        executor=OpenHandsExecutor(manager),
        llm=FakeTeachingLLM(),
    )
    second = await runtime.run_event(
        attempt_id=context["attempt_id"],
        event=event(
            attempt_id=context["attempt_id"],
            learner_id=context["learner_id"],
            state_version=context["state_version"],
            snapshot_id=context["snapshot_id"],
            operation_id=f"stage03-second-{uuid4().hex[:8]}",
            observation="我确认资料已加载，已知问题仍返回空列表，准备检查匹配条件。",
        ),
    )
    if second["help_level"] != "L1" or second["student_failure_count"] != 2:
        raise RuntimeError("second real failure did not produce L1")
    third = await runtime.run_event(
        attempt_id=context["attempt_id"],
        event=event(
            attempt_id=context["attempt_id"],
            learner_id=context["learner_id"],
            state_version=second["state_version"],
            snapshot_id=context["snapshot_id"],
            operation_id=f"stage03-third-{uuid4().hex[:8]}",
            observation="再次验证后仍为空，我已排除资料加载问题并记录了检索返回值。",
        ),
    )
    if "__interrupt__" not in third:
        raise RuntimeError("third failure did not create a teacher interrupt")

    async with factory() as session:
        attempt = await session.get(Attempt, context["attempt_id"])
        interventions = list(
            (
                await session.scalars(
                    select(Intervention).where(
                        Intervention.attempt_id == context["attempt_id"]
                    )
                )
            ).all()
        )
        results = list(
            (
                await session.scalars(
                    select(RequirementResult).where(
                        RequirementResult.attempt_id == context["attempt_id"]
                    )
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(TeachingEvent)
                    .where(TeachingEvent.attempt_id == context["attempt_id"])
                    .order_by(TeachingEvent.created_at)
                )
            ).all()
        )
        ledgers = list(
            (
                await session.scalars(
                    select(OperationLedger).where(
                        OperationLedger.scope == f"tool:{context['attempt_id']}"
                    )
                )
            ).all()
        )
        snapshots = list(
            (
                await session.scalars(
                    select(Snapshot).where(Snapshot.attempt_id == context["attempt_id"])
                )
            ).all()
        )
    waiting = [item for item in interventions if item.status == "WAITING_TEACHER"]
    if len(waiting) != 1:
        raise RuntimeError("business DB does not contain one waiting intervention")
    if len(results) != 3 or any(
        item.snapshot_id != context["snapshot_id"] for item in results
    ):
        raise RuntimeError("requirement result is not bound to the current snapshot")
    if len(ledgers) != 3:
        raise RuntimeError("tool operation ledger did not record three unique executions")
    if attempt.student_failure_count != 3 or attempt.infrastructure_failure_count != 0:
        raise RuntimeError("business failure counters are inconsistent")
    async with checkpoint_saver(checkpoint_url) as saver:
        checkpoint = await saver.aget(runtime.config(context["attempt_id"]))
    if checkpoint is None:
        raise RuntimeError("persistent teaching checkpoint is missing after process restart")

    evidence_files = sorted(
        manager.evidence_directory(context["attempt_id"]).glob("*.artifact.json")
    )
    evidence_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in evidence_files]
    security = evidence_payloads[-1]["details"]["container_security"]
    if security["docker_socket_mounted"]:
        raise RuntimeError("Docker socket was mounted")
    forbidden_keys = {"LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
    if forbidden_keys.intersection(security["container_env_names"]):
        raise RuntimeError("host model key name entered the container")
    if security["network_mode"] != "teachingagent-stage03-internal":
        raise RuntimeError("workspace did not use the internal-only network")
    if security["mounts"] != [
        {"destination": "/workspace/student", "read_write": False, "type": "bind"}
    ]:
        raise RuntimeError("workspace mount set exceeded the current snapshot")

    # Real timeout and output-bound probes use fresh isolated snapshots.
    timeout_attempt = str(uuid4())
    timeout_source = manager.source_directory(timeout_attempt)
    timeout_source.joinpath("faq_app.py").write_text(
        "import time\ntime.sleep(10)\n", encoding="utf-8"
    )
    timeout_snapshot = str(uuid4())
    manager.create_snapshot(attempt_id=timeout_attempt, snapshot_id=timeout_snapshot)
    timeout_result = OpenHandsExecutor(manager).execute(
        ToolExecutionRequest(
            operation_id=f"tool:timeout:{uuid4()}",
            attempt_id=timeout_attempt,
            task_version="FAQ-001-v1",
            stage="implement_retrieval",
            snapshot_id=timeout_snapshot,
            tool_name="run_student_program",
            tool_capability="DIAGNOSTIC",
            timeout_seconds=1,
            resource_policy=ResourcePolicy(),
        )
    )
    if timeout_result.status != "infrastructure_failure":
        raise RuntimeError("timeout was not classified as infrastructure failure")

    output_attempt = str(uuid4())
    output_source = manager.source_directory(output_attempt)
    output_source.joinpath("faq_app.py").write_text(
        "print('x' * 100000)\n", encoding="utf-8"
    )
    output_snapshot = str(uuid4())
    manager.create_snapshot(attempt_id=output_attempt, snapshot_id=output_snapshot)
    output_result = OpenHandsExecutor(manager).execute(
        ToolExecutionRequest(
            operation_id=f"tool:large-output:{uuid4()}",
            attempt_id=output_attempt,
            task_version="FAQ-001-v1",
            stage="implement_retrieval",
            snapshot_id=output_snapshot,
            tool_name="run_student_program",
            tool_capability="DIAGNOSTIC",
            timeout_seconds=10,
            resource_policy=ResourcePolicy(
                max_stdout_bytes=1024, max_stderr_bytes=1024
            ),
        )
    )
    if not output_result.evidence[0].stdout_truncated:
        raise RuntimeError("large output was not truncated and marked")

    missing_result = OpenHandsExecutor(manager).execute(
        ToolExecutionRequest(
            operation_id=f"tool:missing-workspace:{uuid4()}",
            attempt_id=str(uuid4()),
            task_version="FAQ-001-v1",
            stage="implement_retrieval",
            snapshot_id=str(uuid4()),
            tool_name="run_student_program",
            tool_capability="DIAGNOSTIC",
            timeout_seconds=10,
            resource_policy=ResourcePolicy(),
        )
    )
    if missing_result.status != "infrastructure_failure":
        raise RuntimeError("workspace startup failure was not classified as infrastructure")

    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "process_a_pid": context["process_a_pid"],
        "process_b_pid": os.getpid(),
        "attempt_id": context["attempt_id"],
        "snapshot_id": context["snapshot_id"],
        "process_restart_recovered": True,
        "checkpoint_present": True,
        "security_probe": context["security_probe"],
        "first_help_level": "L0",
        "second_help_level": second["help_level"],
        "third_outcome": "teacher_interrupt",
        "student_failure_count": attempt.student_failure_count,
        "infrastructure_failure_count": attempt.infrastructure_failure_count,
        "intervention": {
            "id": waiting[0].id,
            "status": waiting[0].status,
            "requested_state_version": waiting[0].requested_state_version,
        },
        "requirement_results": [
            {
                "requirement_id": item.requirement_id,
                "snapshot_id": item.snapshot_id,
                "operation_id": item.operation_id,
                "status": item.status,
                "evaluator": item.evaluator,
                "evidence_refs": item.evidence_refs,
                "evaluated_at": item.evaluated_at.isoformat(),
                "version": item.version,
            }
            for item in results
        ],
        "timeline": [
            {
                "event_type": item.event_type,
                "operation_id": item.operation_id,
                "state_version": item.state_version,
                "decision": item.payload.get("decision"),
                "help_level": item.payload.get("help_level"),
                "check_status": item.payload.get("check_status"),
            }
            for item in events
        ],
        "snapshot_count": len(snapshots),
        "tool_operation_count": len(ledgers),
        "evidence_artifact_count": len(evidence_files),
        "container_security": security,
        "timeout": {
            "status": timeout_result.status,
            "code": timeout_result.evidence[0].code,
        },
        "large_output": {
            "status": output_result.status,
            "stdout_truncated": output_result.evidence[0].stdout_truncated,
        },
        "missing_workspace": {
            "status": missing_result.status,
            "code": missing_result.evidence[0].code,
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    await engine.dispose()


async def verify_static_check(report_path: Path) -> None:
    business_url, checkpoint_url = environment()
    engine = create_business_engine(business_url)
    factory = create_session_factory(engine)
    service = BusinessService()
    suffix = uuid4().hex[:10]
    async with factory() as session:
        teacher = User(
            email=f"stage03-static-teacher-{suffix}@example.test",
            display_name="Stage03 静态检查教师",
            system_role="teacher",
        )
        learner = User(
            email=f"stage03-static-student-{suffix}@example.test",
            display_name="Stage03 静态检查学生",
            system_role="student",
        )
        course = Course(code=f"STAGE03-STATIC-{suffix}", name="Stage03 Static")
        session.add_all([teacher, learner, course])
        await session.flush()
        session.add_all(
            [
                CourseMembership(
                    course_id=course.id, user_id=teacher.id, role="teacher"
                ),
                CourseMembership(
                    course_id=course.id, user_id=learner.id, role="student"
                ),
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
        static_stage = await session.scalar(
            select(TaskStage).where(
                TaskStage.task_version_id == version.id,
                TaskStage.stage_key == "prepare_sources",
            )
        )
        attempt.current_stage_id = static_stage.id
        await session.commit()
        manager = AttemptWorkspaceManager()
        write_broken_project(manager.source_directory(attempt.id))
        snapshot_id = str(uuid4())
        _, snapshot_ref = manager.create_snapshot(
            attempt_id=attempt.id, snapshot_id=snapshot_id
        )
        await service.register_snapshot(
            session,
            attempt=attempt,
            snapshot_id=snapshot_id,
            snapshot_ref=snapshot_ref,
            sequence=1,
        )

    runtime = PersistentTeachingRuntime(
        session_factory=factory,
        checkpoint_conninfo=checkpoint_url,
        executor=OpenHandsExecutor(manager),
        llm=FakeTeachingLLM(),
    )
    outcome = await runtime.run_event(
        attempt_id=attempt.id,
        event=event(
            attempt_id=attempt.id,
            learner_id=learner.id,
            state_version=0,
            snapshot_id=snapshot_id,
            operation_id=f"stage03-static-{suffix}",
            tool_name="inspect_workspace",
        ),
    )
    async with factory() as session:
        results = list(
            (
                await session.scalars(
                    select(RequirementResult).where(
                        RequirementResult.attempt_id == attempt.id
                    )
                )
            ).all()
        )
    if len(results) != 1 or results[0].status != "SATISFIED":
        raise RuntimeError("real STATIC_CHECK did not produce SATISFIED")
    if outcome["stage_requirements_met"]:
        raise RuntimeError("STATIC_CHECK bypassed the required teacher review")
    artifact_files = sorted(
        manager.evidence_directory(attempt.id).glob("*.artifact.json")
    )
    artifact_payload = json.loads(artifact_files[-1].read_text(encoding="utf-8"))
    security_facts = artifact_payload["details"]["container_security"]
    if security_facts["cap_add"]:
        raise RuntimeError("Agent Server container unexpectedly gained capabilities")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["static_check"] = {
        "attempt_id": attempt.id,
        "snapshot_id": snapshot_id,
        "tool": "inspect_workspace",
        "tool_status": outcome["last_tool_status"],
        "requirement_id": results[0].requirement_id,
        "requirement_status": results[0].status,
        "operation_id": results[0].operation_id,
        "evidence_refs": results[0].evidence_refs,
        "stage_requirements_met": outcome["stage_requirements_met"],
        "stage_advanced": outcome["current_stage"] != "prepare_sources",
        "agent_container_user": security_facts["container_user"],
        "agent_cap_add": security_facts["cap_add"],
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["static_check"], ensure_ascii=False))
    await engine.dispose()


async def verify_idempotency_and_timeout(context_path: Path, report_path: Path) -> None:
    business_url, checkpoint_url = environment()
    context = json.loads(context_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manager = AttemptWorkspaceManager()
    executor = OpenHandsExecutor(manager)
    first_requirement = report["requirement_results"][0]
    before_artifacts = len(
        list(manager.evidence_directory(context["attempt_id"]).glob("*.artifact.json"))
    )
    replay = executor.execute(
        ToolExecutionRequest(
            operation_id=first_requirement["operation_id"],
            attempt_id=context["attempt_id"],
            task_version="FAQ-001-v1",
            stage="implement_retrieval",
            snapshot_id=context["snapshot_id"],
            tool_name="run_faq_tests",
            tool_capability="EVALUATION",
            timeout_seconds=45,
            resource_policy=ResourcePolicy(),
        )
    )
    after_artifacts = len(
        list(manager.evidence_directory(context["attempt_id"]).glob("*.artifact.json"))
    )
    if not replay.duplicate or before_artifacts != after_artifacts:
        raise RuntimeError("real operation replay produced a new execution artifact")

    engine = create_business_engine(business_url)
    factory = create_session_factory(engine)
    service = BusinessService()
    suffix = uuid4().hex[:10]
    async with factory() as session:
        teacher = User(
            email=f"stage03-timeout-teacher-{suffix}@example.test",
            display_name="Stage03 超时教师",
            system_role="teacher",
        )
        learner = User(
            email=f"stage03-timeout-student-{suffix}@example.test",
            display_name="Stage03 超时学生",
            system_role="student",
        )
        course = Course(code=f"STAGE03-TIMEOUT-{suffix}", name="Stage03 Timeout")
        session.add_all([teacher, learner, course])
        await session.flush()
        session.add_all(
            [
                CourseMembership(
                    course_id=course.id, user_id=teacher.id, role="teacher"
                ),
                CourseMembership(
                    course_id=course.id, user_id=learner.id, role="student"
                ),
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
        retrieval_stage = await session.scalar(
            select(TaskStage).where(
                TaskStage.task_version_id == version.id,
                TaskStage.stage_key == "implement_retrieval",
            )
        )
        attempt.current_stage_id = retrieval_stage.id
        await session.commit()
        source = manager.source_directory(attempt.id)
        source.joinpath("faq_app.py").write_text(
            "import time\ntime.sleep(10)\n", encoding="utf-8"
        )
        snapshot_id = str(uuid4())
        _, snapshot_ref = manager.create_snapshot(
            attempt_id=attempt.id, snapshot_id=snapshot_id
        )
        await service.register_snapshot(
            session,
            attempt=attempt,
            snapshot_id=snapshot_id,
            snapshot_ref=snapshot_ref,
            sequence=1,
        )

    runtime = PersistentTeachingRuntime(
        session_factory=factory,
        checkpoint_conninfo=checkpoint_url,
        executor=executor,
        llm=FakeTeachingLLM(),
        tool_timeouts={"run_student_program": 1},
    )
    timeout_outcome = await runtime.run_event(
        attempt_id=attempt.id,
        event=event(
            attempt_id=attempt.id,
            learner_id=learner.id,
            state_version=0,
            snapshot_id=snapshot_id,
            operation_id=f"stage03-timeout-graph-{suffix}",
            tool_name="run_student_program",
        ),
    )
    async with factory() as session:
        persisted = await session.get(Attempt, attempt.id)
    if (
        timeout_outcome["student_failure_count"] != 0
        or timeout_outcome["infrastructure_failure_count"] != 1
        or persisted.student_failure_count != 0
        or persisted.infrastructure_failure_count != 1
    ):
        raise RuntimeError("real OpenHands timeout affected the wrong failure counter")
    report["real_idempotency"] = {
        "operation_id": first_requirement["operation_id"],
        "duplicate": replay.duplicate,
        "artifacts_before": before_artifacts,
        "artifacts_after": after_artifacts,
    }
    report["timeout_graph"] = {
        "attempt_id": attempt.id,
        "check_status": timeout_outcome["latest_check_status"],
        "student_failure_count": persisted.student_failure_count,
        "infrastructure_failure_count": persisted.infrastructure_failure_count,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "real_idempotency": report["real_idempotency"],
                "timeout_graph": report["timeout_graph"],
            },
            ensure_ascii=False,
        )
    )
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["first", "continue", "static", "failure-audit"]
    )
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if args.phase == "first":
        asyncio.run(seed(args.context))
    elif args.phase == "continue":
        if args.report is None:
            parser.error("--report is required for continue")
        asyncio.run(continue_flow(args.context, args.report))
    elif args.phase == "static":
        if args.report is None:
            parser.error("--report is required for static")
        asyncio.run(verify_static_check(args.report))
    else:
        if args.report is None:
            parser.error("--report is required for failure-audit")
        asyncio.run(verify_idempotency_and_timeout(args.context, args.report))


if __name__ == "__main__":
    main()
