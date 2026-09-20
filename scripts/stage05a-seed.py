"""Create real FAQ-001-v1 browser-E2E facts and one waiting intervention."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select

from app.agent.teaching_llm import DeepSeekTeachingLLM
from app.agent.tools import OpenHandsExecutor
from app.agent.workspace import AttemptWorkspaceManager
from app.business.database import create_business_engine, create_session_factory
from app.business.models import Attempt, Course, CourseMembership, Intervention, TaskStage, User
from app.business.service import BusinessService
from app.business.stage03 import PersistentTeachingRuntime


def environment() -> tuple[str, str]:
    business = os.environ.get("TEACHING_DATABASE_URL", "")
    checkpoint = os.environ.get("LANGGRAPH_CHECKPOINT_DATABASE_URL") or os.environ.get("CHECKPOINT_DATABASE_URL", "")
    if not business or not checkpoint:
        raise RuntimeError("TEACHING_DATABASE_URL and LANGGRAPH_CHECKPOINT_DATABASE_URL are required")
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    return business, checkpoint


def write_broken_project(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data").mkdir(exist_ok=True)
    sources = [
        {"question": "如何重置密码？", "answer": "在账户安全页选择重置密码。", "source": "faq://account/reset-password"},
        {"question": "如何联系人工客服？", "answer": "在帮助中心提交工单。", "source": "faq://support/ticket"},
        {"question": "退款需要多久？", "answer": "审核通过后五个工作日内到账。", "source": "faq://billing/refund"},
    ]
    (directory / "data" / "faq.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "faq_app.py").write_text(
        "import json\n"
        "from pathlib import Path\n\n"
        "def load_sources(path):\n"
        "    return json.loads(Path(path).read_text(encoding='utf-8'))\n\n"
        "def retrieve(question, sources):\n"
        "    # TODO: 根据问题文本从资料中检索匹配项\n"
        "    return []\n",
        encoding="utf-8",
    )
    (directory / "README.md").write_text(
        "# FAQ 检索实训\n\n实现 `retrieve`，让已知问题命中，并保持未知问题返回空列表。\n",
        encoding="utf-8",
    )


def graph_event(*, attempt: Attempt, learner_id: str, snapshot_id: str, operation: str, observation: str = "") -> dict:
    return {
        "event_id": str(uuid4()), "event_type": "run_tool", "attempt_id": attempt.id,
        "task_version": "FAQ-001-v1", "actor_id": learner_id, "actor_role": "student",
        "expected_state_version": attempt.state_version, "operation_id": operation,
        "snapshot_id": snapshot_id, "evidence_refs": [], "evidence_summary": "",
        "student_observation": observation, "failure_origin": "none",
        "requested_guidance_kind": "question", "requested_tool": "run_faq_tests",
    }


async def create_attempt(session, service: BusinessService, manager: AttemptWorkspaceManager, *, version_id: str, learner: User) -> tuple[Attempt, str]:
    attempt = await service.create_attempt(session, task_version_id=version_id, learner_id=learner.id, mode="guided_practice")
    stage = await session.scalar(select(TaskStage).where(TaskStage.task_version_id == version_id, TaskStage.stage_key == "implement_retrieval"))
    if stage is None:
        raise RuntimeError("implement_retrieval stage missing")
    attempt.current_stage_id = stage.id
    await session.commit()
    write_broken_project(manager.source_directory(attempt.id))
    snapshot_id = str(uuid4())
    _, snapshot_ref = manager.create_snapshot(attempt_id=attempt.id, snapshot_id=snapshot_id)
    await service.register_snapshot(session, attempt=attempt, snapshot_id=snapshot_id, snapshot_ref=snapshot_ref, sequence=1)
    return attempt, snapshot_id


async def main() -> None:
    business_url, checkpoint_url = environment()
    engine = create_business_engine(business_url)
    factory = create_session_factory(engine)
    service = BusinessService()
    manager = AttemptWorkspaceManager()
    suffix = uuid4().hex[:8]
    async with factory() as session:
        teacher = User(email=f"stage05a.teacher.{suffix}@example.test", display_name="陈老师", system_role="teacher")
        learner = User(email=f"stage05a.student.{suffix}@example.test", display_name="林雨", system_role="student")
        blocked = User(email=f"stage05a.blocked.{suffix}@example.test", display_name="周宁", system_role="student")
        course = Course(code=f"AI-APP-STAGE05A-{suffix}", name="AI 应用开发实训")
        session.add_all([teacher, learner, blocked, course]); await session.flush()
        session.add_all([
            CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
            CourseMembership(course_id=course.id, user_id=learner.id, role="student"),
            CourseMembership(course_id=course.id, user_id=blocked.id, role="student"),
        ])
        await session.commit()
        version = await service.create_faq_version(session, course_id=course.id, actor_id=teacher.id)
        student_attempt, student_snapshot = await create_attempt(session, service, manager, version_id=version.id, learner=learner)
        blocked_attempt, blocked_snapshot = await create_attempt(session, service, manager, version_id=version.id, learner=blocked)

    runtime = PersistentTeachingRuntime(
        session_factory=factory, checkpoint_conninfo=checkpoint_url,
        executor=OpenHandsExecutor(manager), llm=DeepSeekTeachingLLM(), service=service,
    )
    observation = "资料文件能够正常加载；已知问题应命中密码重置条目，但当前返回空列表；未知问题保持空列表。"
    for index in range(1, 4):
        async with factory() as session:
            current = await session.get(Attempt, blocked_attempt.id)
        await runtime.run_event(
            attempt_id=current.id,
            event=graph_event(
                attempt=current, learner_id=blocked.id, snapshot_id=blocked_snapshot,
                operation=f"stage05a-blocked-{index}-{uuid4().hex[:8]}",
                observation=observation if index >= 2 else "",
            ),
        )

    async with factory() as session:
        waiting = await session.scalar(select(Intervention).where(
            Intervention.attempt_id == blocked_attempt.id,
            Intervention.status == "WAITING_TEACHER",
        ).order_by(Intervention.created_at.desc()).limit(1))
        if waiting is None:
            raise RuntimeError("real waiting intervention was not created")
        current_blocked = await session.get(Attempt, blocked_attempt.id)
    context = {
        "created_at": datetime.now(UTC).isoformat(),
        "course_id": course.id,
        "teacher_id": teacher.id,
        "student": {"user_id": learner.id, "attempt_id": student_attempt.id, "snapshot_a": student_snapshot},
        "teacher_case": {"user_id": blocked.id, "attempt_id": blocked_attempt.id, "snapshot_a": blocked_snapshot,
                         "intervention_id": waiting.id, "state_version": current_blocked.state_version},
    }
    runtime_dir = ROOT / ".runtime"
    runtime_dir.mkdir(exist_ok=True)
    (runtime_dir / "stage05a-context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "stage05a-seed.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(context, ensure_ascii=False, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
