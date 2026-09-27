"""Initialize or reset the isolated Stage 06 FAQ demonstration namespace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import psycopg
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.agent.workspace import AttemptWorkspaceManager
from app.business.faq import DEFAULT_TASK_POLICY, FAQ_RUBRIC, FAQ_STAGES
from app.business.models import (
    Attempt,
    Course,
    CourseMembership,
    FormalGrade,
    Intervention,
    OperationLedger,
    RequirementDefinition,
    RequirementResult,
    Review,
    ReviewItem,
    RubricDefinition,
    Snapshot,
    Submission,
    Task,
    TaskStage,
    TaskVersion,
    TeachingEvent,
    User,
)

COURSE_CODE = "DEMO-FAQ-001-RC06"
NAMESPACE = uuid.UUID("f15be2bb-4427-4a94-92be-9ee0e6690606")
CONFIRMATION = "RESET_DEMO_RC06"
ATTEMPT_ROOT = (ROOT / "workspaces" / "attempts").resolve()
EVIDENCE_ROOT = (ROOT / "workspaces" / "evidence").resolve()
MODEL_ROOT = (ROOT / "workspaces" / "model-runs").resolve()
LOGIN_FILE = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "teachingagent-stage06-demo-login.json"
WORKSPACE_MANAGER = AttemptWorkspaceManager(ATTEMPT_ROOT, EVIDENCE_ROOT)


def ensure_demo_credentials() -> dict[str, str]:
    if LOGIN_FILE.exists():
        payload = json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
        required = {"student_account", "student_password", "teacher_account", "teacher_password"}
        if required.issubset(payload) and all(payload[key] for key in required):
            extra = {
                "teacher2_account": "demo_teacher2", "teacher2_password": "123456",
                "teacher3_account": "demo_teacher3", "teacher3_password": "123456",
            }
            if any(not payload.get(key) for key in extra):
                payload.update({key: payload.get(key) or value for key, value in extra.items()})
                LOGIN_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return payload
    payload = {
        "student_account": "demo_student",
        "student_password": "123456",
        "teacher_account": "demo_teacher",
        "teacher_password": "123456",
        "teacher2_account": "demo_teacher2",
        "teacher2_password": "123456",
        "teacher3_account": "demo_teacher3",
        "teacher3_password": "123456",
    }
    LOGIN_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload




def stable_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


IDS = {
    "teacher": stable_id("teacher"), "student": stable_id("student"),
    "teacher2": stable_id("teacher-2"), "teacher3": stable_id("teacher-3"),
    "course": stable_id("course"), "task": stable_id("task"),
    "version": stable_id("version"), "attempt": stable_id("attempt"),
}


def local_urls() -> tuple[str, str]:
    info = json.loads((Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8"))
    business = (
        f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}"
        f"@{info['host']}:{info['port']}/{info['database']}"
    )
    checkpoint = (
        f"postgresql://langgraph_cp:{quote_plus(info['langgraph_cp_password'])}"
        f"@{info['host']}:{info['port']}/{info['database']}"
    )
    return business, checkpoint


def guard(database_url: str) -> None:
    environment = os.getenv("TEACHING_ENV", "").upper()
    if environment not in {"TEST", "DEMO"}:
        raise RuntimeError("Refusing Stage 06 demo data operation unless TEACHING_ENV=TEST or DEMO")
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql" or parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Stage 06 demo data requires a loopback PostgreSQL instance")
    if any(marker in (parsed.database or "").lower() for marker in ("prod", "production")):
        raise RuntimeError("Refusing a production-named database")


def attempt_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def workspace_source() -> Path:
    return ATTEMPT_ROOT / ("attempt-" + attempt_digest(IDS["attempt"])) / "source"


def _virtual_workspace(attempt_id: str) -> None:
    source = ATTEMPT_ROOT / ("attempt-" + attempt_digest(attempt_id)) / "source"
    source.mkdir(parents=True, exist_ok=True)
    target = source / "faq_app.py"
    if not target.exists():
        target.write_text(
            "def retrieve(question, sources):\n"
            "    return [item for item in sources if item.get('question') == question]\n",
            encoding="utf-8",
        )
    data_dir = source / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    faq_path = data_dir / "faq.json"
    if not faq_path.exists():
        faq_path.write_text(json.dumps([
            {"id": "password-reset", "question": "如何重置密码？", "keywords": ["重置密码", "密码", "账号"], "answer": "在登录页选择‘忘记密码’，完成身份验证后即可设置新密码。", "source_title": "校园信息中心服务指南", "source": "https://school.example.invalid/account", "authority": "校园信息中心", "scope": "本校学生服务"},
            {"id": "lab-hours", "question": "实训室开放时间是什么？", "keywords": ["实训室", "开放时间", "工作日"], "answer": "工作日 8:30—17:30 开放，节假日需提前预约。", "source_title": "实训室使用手册", "source": "https://school.example.invalid/lab-hours", "authority": "实训中心", "scope": "本校实训室"},
            {"id": "submission", "question": "作业如何提交？", "keywords": ["作业", "提交", "Snapshot"], "answer": "完成运行检查后保存 Snapshot，再点击提交作品。", "source_title": "课程作业提交说明", "source": "https://school.example.invalid/submission", "authority": "课程组", "scope": "本课程作业"},
        ], ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = data_dir / "source_manifest.json"
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps([
            {"id": "campus-account", "title": "校园信息中心服务指南", "url": "https://school.example.invalid/account", "authority": "校园信息中心", "scope": "本校学生服务"},
            {"id": "lab-hours", "title": "实训室使用手册", "url": "https://school.example.invalid/lab-hours", "authority": "实训中心", "scope": "本校实训室"},
            {"id": "submission", "title": "课程作业提交说明", "url": "https://school.example.invalid/submission", "authority": "课程组", "scope": "本课程作业"},
        ], ensure_ascii=False, indent=2), encoding="utf-8")
    readme = source / "README.md"
    if not readme.exists():
        readme.write_text("# 校园服务问答小程序\n\n教师演示用学生工作区。\n", encoding="utf-8")


def _virtual_submission_records(
    session: Session,
    *,
    key: str,
    attempt: Attempt,
    snapshot: Snapshot,
    created_at: datetime,
    completed: bool,
) -> None:
    """Seed one pending or published review without bypassing the normal view contracts."""
    submission_id = stable_id("virtual-submission-" + key)
    submission = session.get(Submission, submission_id)
    if submission is None:
        submission = Submission(
            id=submission_id,
            attempt_id=attempt.id,
            snapshot_id=snapshot.id,
            operation_id=f"demo-virtual:{key}:submit",
            sequence=1,
            status="submitted",
            explanation="已完成当前版本实现，请教师依据 Snapshot 和检查结果复核。",
            assistance=[],
            created_at=created_at,
        )
        session.add(submission)
    else:
        submission.attempt_id = attempt.id
        submission.snapshot_id = snapshot.id
        submission.created_at = created_at

    review_id = stable_id("virtual-review-" + key)
    review = session.get(Review, review_id)
    if review is None:
        review = Review(
            id=review_id,
            submission_id=submission.id,
            teacher_id=IDS["teacher"],
            status="published" if completed else "draft",
            created_at=created_at,
        )
        session.add(review)
    review.submission_id = submission.id
    review.teacher_id = IDS["teacher"]
    review.status = "published" if completed else "draft"
    if completed:
        review.publish_operation_id = f"demo-virtual:{key}:publish"
        review.published_at = created_at + timedelta(minutes=20)

    session.flush()
    rubric = list(session.scalars(
        select(RubricDefinition)
        .where(RubricDefinition.task_version_id == IDS["version"])
        .order_by(RubricDefinition.position)
    ))
    for index, definition in enumerate(rubric):
        item = session.scalar(select(ReviewItem).where(
            ReviewItem.review_id == review.id,
            ReviewItem.rubric_key == definition.item_key,
        ))
        if item is None:
            item = ReviewItem(
                id=stable_id(f"virtual-review-item-{key}-{definition.item_key}"),
                review_id=review.id,
                rubric_key=definition.item_key,
                ai_text="建议结合提交说明和可复核证据确认。",
                ai_evidence_refs=[f"snapshot://{snapshot.id}"],
            )
            session.add(item)
        item.auto_status = "SATISFIED" if completed else "NOT_RUN"
        item.auto_summary = "演示检查证据已满足。" if completed else "等待教师核对提交证据。"
        item.ai_evidence_refs = [f"snapshot://{snapshot.id}"]
        item.teacher_score = max(0, definition.max_score - (2 if index == 1 else 0)) if completed else None
        item.teacher_reason = "已核对提交说明与检查证据。" if completed else ""
        item.teacher_confirmed = completed

    if completed:
        grade = session.scalar(select(FormalGrade).where(FormalGrade.submission_id == submission.id))
        max_score = sum(int(item.max_score) for item in rubric)
        if grade is None:
            grade = FormalGrade(
                id=stable_id("virtual-grade-" + key),
                submission_id=submission.id,
                review_id=review.id,
                total_score=max_score - 2,
                max_score=max_score,
                published_by=IDS["teacher"],
                change_reason="演示班已完成一次教师复核。",
                published_at=created_at + timedelta(minutes=20),
            )
            session.add(grade)
        ledger_scope = f"review-publish:{review.id}"
        if session.scalar(select(OperationLedger).where(
            OperationLedger.scope == ledger_scope,
            OperationLedger.operation_id == f"demo-virtual:{key}:publish",
        )) is None:
            session.add(OperationLedger(
                scope=ledger_scope,
                operation_id=f"demo-virtual:{key}:publish",
                status="COMPLETED",
                result_ref=f"formal-grade:{grade.id}",
                result_payload={"total_score": grade.total_score, "max_score": grade.max_score},
            ))


def seed(session: Session) -> dict[str, str]:
    teacher = session.get(User, IDS["teacher"])
    if teacher is None:
        teacher = User(id=IDS["teacher"], email="teacher.rc06@demo.invalid", display_name="演示教师", system_role="teacher")
        session.add(teacher)
    for suffix, label in (("teacher2", "演示教师二"), ("teacher3", "演示教师三")):
        if session.get(User, IDS[suffix]) is None:
            session.add(User(id=IDS[suffix], email=f"{suffix}.rc06@demo.invalid",
                             display_name=label, system_role="teacher"))
    student = session.get(User, IDS["student"])
    if student is None:
        student = User(id=IDS["student"], email="student.rc06@demo.invalid", display_name="演示学生", system_role="student")
        session.add(student)
    course = session.get(Course, IDS["course"])
    if course is None:
        course = Course(id=IDS["course"], code=COURSE_CODE, name="校园服务问答课堂")
        session.add(course)
    else:
        course.name = "校园服务问答课堂"
    for user_id, role, suffix in (
        (IDS["teacher"], "teacher", "teacher"),
        (IDS["teacher2"], "teacher", "teacher2"),
        (IDS["teacher3"], "teacher", "teacher3"),
        (IDS["student"], "student", "student"),
    ):
        membership_id = stable_id("membership-" + suffix)
        if session.get(CourseMembership, membership_id) is None:
            session.add(CourseMembership(id=membership_id, course_id=course.id, user_id=user_id, role=role))
    task = session.get(Task, IDS["task"])
    if task is None:
        task = Task(id=IDS["task"], course_id=course.id, task_key="FAQ-001", title="校园服务问答小程序")
        session.add(task)
    else:
        task.title = "校园服务问答小程序"
    version = session.get(TaskVersion, IDS["version"])
    if version is None:
        policy = dict(DEFAULT_TASK_POLICY)
        policy["demo_namespace"] = "stage06-rc1"
        version = TaskVersion(id=IDS["version"], task_id=task.id, version="v1", status="published", policy=policy)
        session.add(version)
    session.flush()
    stage_ids: dict[str, str] = {}
    for position, (stage_key, title, requirements) in enumerate(FAQ_STAGES):
        stage_id = stable_id("stage-" + stage_key)
        stage_ids[stage_key] = stage_id
        if session.get(TaskStage, stage_id) is None:
            session.add(TaskStage(id=stage_id, task_version_id=version.id, stage_key=stage_key, title=title, position=position, aggregation="ALL_REQUIRED"))
        for req_key, kind, evaluator, config in requirements:
            req_id = stable_id("requirement-" + req_key)
            if session.get(RequirementDefinition, req_id) is None:
                session.add(RequirementDefinition(id=req_id, task_stage_id=stage_id, requirement_key=req_key, kind=kind, required=True, version=1, evaluator=evaluator, config=config))
    for position, (key, title, max_score, requirement_keys) in enumerate(FAQ_RUBRIC):
        rubric_id = stable_id("rubric-" + key)
        if session.get(RubricDefinition, rubric_id) is None:
            session.add(RubricDefinition(id=rubric_id, task_version_id=version.id, item_key=key, title=title, max_score=max_score, position=position, requirement_keys=list(requirement_keys)))
    attempt = session.get(Attempt, IDS["attempt"])
    if attempt is None:
        attempt = Attempt(id=IDS["attempt"], task_version_id=version.id, learner_id=student.id, current_stage_id=stage_ids["understand_requirements"], mode="guided_practice", status="active")
        session.add(attempt)
    ledger = session.scalar(select(OperationLedger).where(OperationLedger.scope == "demo-namespace", OperationLedger.operation_id == "stage06:rc1"))
    if ledger is None:
        session.add(OperationLedger(scope="demo-namespace", operation_id="stage06:rc1", status="COMPLETED", result_ref=f"attempt:{attempt.id}", result_payload={"course_code": COURSE_CODE, "namespace": "stage06-rc1"}))
    # Add stable, read-only classroom activity so the teacher view can demonstrate
    # meaningful distributions without relying on a real class or fabricated grades.
    virtual_profiles = (
        ("lin", "林子轩", "implement_retrieval", "failed", -1),
        ("zhou", "周宁", "prepare_sources", "passed", -2),
        ("wang", "王晨", "generate_cited_answer", "unchecked", -3),
        ("zhao", "赵宇", "validate_boundaries", "help", -4),
        ("chen", "陈思远", "deliver", "completed", -5),
        ("fang", "方可", "understand_requirements", "passed", -6),
        ("wu", "吴桐", None, "unstarted", -7),
    )
    now = datetime.now(UTC)
    for key, display_name, stage_key, state, days_ago in virtual_profiles:
        virtual_id = stable_id("virtual-student-" + key)
        virtual = session.get(User, virtual_id)
        if virtual is None:
            virtual = User(id=virtual_id, email=f"{key}.virtual@demo.invalid", display_name=display_name, system_role="student")
            session.add(virtual)
        membership_id = stable_id("virtual-membership-" + key)
        if session.get(CourseMembership, membership_id) is None:
            session.add(CourseMembership(id=membership_id, course_id=course.id, user_id=virtual_id, role="student"))
        if stage_key is None:
            continue
        virtual_attempt_id = stable_id("virtual-attempt-" + key)
        virtual_attempt = session.get(Attempt, virtual_attempt_id)
        if virtual_attempt is None:
            virtual_attempt = Attempt(
                id=virtual_attempt_id, task_version_id=version.id, learner_id=virtual_id,
                current_stage_id=stage_ids[stage_key], mode="guided_practice",
                status="completed" if state == "completed" else "active",
                student_failure_count=3 if state == "failed" else (4 if state == "help" else 0),
                created_at=now + timedelta(days=days_ago),
            )
            session.add(virtual_attempt)
        else:
            virtual_attempt.current_stage_id = stage_ids[stage_key]
            virtual_attempt.status = "completed" if state == "completed" else "active"
            virtual_attempt.student_failure_count = 3 if state == "failed" else (4 if state == "help" else 0)
            virtual_attempt.created_at = now + timedelta(days=days_ago)
        snapshot_id = stable_id("virtual-snapshot-" + key)
        if session.get(Snapshot, snapshot_id) is None:
            session.add(Snapshot(
                id=snapshot_id, attempt_id=virtual_attempt_id, snapshot_ref=f"demo-{key}-snapshot",
                sequence=1, created_at=now + timedelta(days=days_ago),
            ))
        session.flush()
        snapshot = session.get(Snapshot, snapshot_id)
        if snapshot is not None:
            _virtual_workspace(virtual_attempt_id)
            _, snapshot.snapshot_ref = WORKSPACE_MANAGER.create_snapshot(
                attempt_id=virtual_attempt_id,
                snapshot_id=snapshot_id,
            )
            if state in {"completed", "unchecked"}:
                _virtual_submission_records(
                    session,
                    key=key,
                    attempt=virtual_attempt,
                    snapshot=snapshot,
                    created_at=now + timedelta(days=days_ago, minutes=15),
                    completed=state == "completed",
                )
        if state in {"failed", "passed"}:
            requirements = next(item[2] for item in FAQ_STAGES if item[0] == stage_key)
            for index, (req_key, _kind, evaluator, _config) in enumerate(requirements):
                req_id = stable_id("requirement-" + req_key)
                operation_id = f"demo-virtual:{key}:check:{index}"
                result = session.scalar(select(RequirementResult).where(
                    RequirementResult.attempt_id == virtual_attempt_id, RequirementResult.operation_id == operation_id
                ))
                status = "NOT_SATISFIED" if state == "failed" and index == 0 else "SATISFIED"
                if result is None:
                    session.add(RequirementResult(
                        id=stable_id(f"virtual-result-{key}-{index}"), attempt_id=virtual_attempt_id,
                        requirement_id=req_id, snapshot_id=snapshot_id, operation_id=operation_id,
                        status=status, evaluator=evaluator, evidence_refs=[f"evidence://demo/{key}/{index}"],
                        version=1, evaluated_at=now + timedelta(days=days_ago),
                    ))
                else:
                    result.status = status
        if state == "help":
            event_operation = f"demo-virtual:{key}:help"
            if session.scalar(select(TeachingEvent).where(TeachingEvent.attempt_id == virtual_attempt_id, TeachingEvent.operation_id == event_operation)) is None:
                session.add(TeachingEvent(
                    id=stable_id(f"virtual-help-event-{key}"), attempt_id=virtual_attempt_id, actor_id=virtual_id,
                    event_type="help_requested", operation_id=event_operation, state_version=0,
                    payload={"reason": "连续检查未通过，学生请求教师帮助"}, created_at=now + timedelta(days=days_ago),
                ))
            intervention_operation = f"demo-virtual:{key}:intervention"
            if session.scalar(select(Intervention).where(Intervention.operation_id == intervention_operation)) is None:
                session.add(Intervention(
                    id=stable_id(f"virtual-intervention-{key}"), attempt_id=virtual_attempt_id,
                    operation_id=intervention_operation, reason="student_requested_help",
                    status="WAITING_TEACHER", requested_state_version=0,
                    evidence_refs=[f"evidence://demo/{key}/help"], allow_l2=True,
                    created_at=now + timedelta(days=days_ago),
                ))
    session.commit()
    source = workspace_source()
    source.mkdir(parents=True, exist_ok=True)
    files = {
        "faq_app.py": """import json\nfrom pathlib import Path\n\ndef load_sources(path: str) -> list[dict]:\n    payload = json.loads(Path(path).read_text(encoding=\"utf-8\"))\n    if not isinstance(payload, list):\n        raise TypeError(\"资料必须是列表\")\n    return payload\n\ndef retrieve(question: str, sources: list[dict]) -> list[dict]:\n    # 演示故意留下一个小问题：资料已加载，但还没有完成匹配。\n    return []\n\ndef answer(question: str, sources: list[dict]) -> dict:\n    hits = retrieve(question, sources)\n    if not hits:\n        return {\"answer\": \"暂未找到可靠答案\", \"citations\": []}\n    first = hits[0]\n    return {\"answer\": first[\"answer\"], \"citations\": [{\"title\": first[\"source_title\"], \"url\": first[\"source\"]}]}\n\nif __name__ == \"__main__\":\n    data = load_sources(str(Path(__file__).parent / \"data\" / \"faq.json\"))\n    print(answer(\"如何重置密码？\", data))\n""",
        "data/faq.json": json.dumps([
            {"id": "password-reset", "question": "如何重置密码？", "keywords": ["重置密码", "密码", "账号"], "answer": "在登录页选择‘忘记密码’，完成身份验证后即可设置新密码。", "source_title": "校园信息中心服务指南", "source": "https://school.example.invalid/account", "authority": "校园信息中心", "scope": "本校学生服务"},
            {"id": "lab-hours", "question": "实训室开放时间是什么？", "keywords": ["实训室", "开放时间", "工作日"], "answer": "工作日 8:30—17:30 开放，节假日需提前预约。", "source_title": "实训室使用手册", "source": "https://school.example.invalid/lab-hours", "authority": "实训中心", "scope": "本校实训室"},
            {"id": "submission", "question": "作业如何提交？", "keywords": ["作业", "提交", "Snapshot"], "answer": "完成运行检查后保存 Snapshot，再点击提交作品。", "source_title": "课程作业提交说明", "source": "https://school.example.invalid/submission", "authority": "课程组", "scope": "本课程作业"},
        ], ensure_ascii=False, indent=2),
        "data/source_manifest.json": json.dumps([
            {"id": "campus-account", "title": "校园信息中心服务指南", "url": "https://school.example.invalid/account", "authority": "校园信息中心", "scope": "本校学生服务"},
            {"id": "lab-hours", "title": "实训室使用手册", "url": "https://school.example.invalid/lab-hours", "authority": "实训中心", "scope": "本校实训室"},
            {"id": "submission", "title": "课程作业提交说明", "url": "https://school.example.invalid/submission", "authority": "课程组", "scope": "本课程作业"},
        ], ensure_ascii=False, indent=2),
        "README.md": "# 校园服务问答小程序\n\n这是一个面向校园服务的最小 FAQ 任务。请在 `faq_app.py` 中完成 `retrieve(question, sources)`：命中资料时返回对应记录，资料外问题必须返回空列表，并在回答中保留来源链接。\n\n运行：`python faq_app.py`。\n",
    }
    for name, content in files.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {**IDS, "course_code": COURSE_CODE, "workspace": "isolated-demo-workspace", "virtual_students": len(virtual_profiles)}


def reset(session: Session) -> dict[str, int]:
    attempt_ids = list(session.scalars(select(Attempt.id).join(TaskVersion).join(Task).where(Task.course_id == IDS["course"])))
    counts = {name: 0 for name in ("attempts", "snapshots", "requirement_results", "interventions", "submissions", "reviews", "formal_grades", "evidence")}
    if not attempt_ids:
        return counts
    submission_ids = list(session.scalars(select(Submission.id).where(Submission.attempt_id.in_(attempt_ids))))
    review_ids = list(session.scalars(select(Review.id).where(Review.submission_id.in_(submission_ids)))) if submission_ids else []
    intervention_operations = list(session.scalars(select(Intervention.operation_id).where(Intervention.attempt_id.in_(attempt_ids))))
    def remove(model, condition, key):
        result = session.execute(delete(model).where(condition)); counts[key] = int(result.rowcount or 0)
    if submission_ids:
        remove(FormalGrade, FormalGrade.submission_id.in_(submission_ids), "formal_grades")
    if review_ids:
        session.execute(delete(ReviewItem).where(ReviewItem.review_id.in_(review_ids)))
        remove(Review, Review.id.in_(review_ids), "reviews")
    if submission_ids:
        remove(Submission, Submission.id.in_(submission_ids), "submissions")
    remove(RequirementResult, RequirementResult.attempt_id.in_(attempt_ids), "requirement_results")
    session.execute(delete(TeachingEvent).where(TeachingEvent.attempt_id.in_(attempt_ids)))
    remove(Intervention, Intervention.attempt_id.in_(attempt_ids), "interventions")
    remove(Snapshot, Snapshot.attempt_id.in_(attempt_ids), "snapshots")
    ledger_scopes = [
        *(f"attempt:{item}" for item in attempt_ids),
        *(f"snapshot:{item}" for item in attempt_ids),
        *(f"submission:{item}" for item in attempt_ids),
        *(f"requirement:{item}" for item in attempt_ids),
        *(f"teacher-review:{item}" for item in submission_ids),
        *(f"review-publish:{item}" for item in review_ids),
    ]
    session.execute(delete(OperationLedger).where(
        (OperationLedger.scope == "demo-namespace") |
        (OperationLedger.scope.in_(ledger_scopes)) |
        ((OperationLedger.scope == "intervention") & OperationLedger.operation_id.in_(intervention_operations))
    ))
    remove(Attempt, Attempt.id.in_(attempt_ids), "attempts")
    session.commit()
    for attempt_id in attempt_ids:
        digest = "attempt-" + attempt_digest(attempt_id)
        for root in (ATTEMPT_ROOT, EVIDENCE_ROOT, MODEL_ROOT):
            target = (root / digest).resolve()
            if root not in target.parents:
                raise RuntimeError("Refusing path outside demo workspace roots")
            if target.exists():
                shutil.rmtree(target); counts["evidence"] += 1
    return counts


def reset_checkpoint(checkpoint_url: str | None, attempt_ids: list[str]) -> int:
    if not checkpoint_url or not attempt_ids:
        return 0
    removed = 0
    with psycopg.connect(checkpoint_url, autocommit=True) as connection:
        for table in ("checkpoint_writes", "checkpoints", "checkpoint_blobs"):
            result = connection.execute(
                f"DELETE FROM {table} WHERE thread_id = ANY(%s)", (attempt_ids,)
            )
            removed += result.rowcount or 0
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "reset"])
    parser.add_argument("--database-url", default=os.getenv("TEACHING_DATABASE_URL"))
    parser.add_argument("--local-stage02b-config", action="store_true")
    parser.add_argument("--checkpoint-url", default=os.getenv("LANGGRAPH_CHECKPOINT_DATABASE_URL"))
    parser.add_argument("--confirm")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    local_business, local_checkpoint = local_urls() if args.local_stage02b_config else (None, None)
    database_url = local_business or args.database_url
    checkpoint_url = local_checkpoint or args.checkpoint_url
    if not database_url:
        raise RuntimeError("Provide --database-url or --local-stage02b-config")
    guard(database_url)
    if args.action == "reset" and args.confirm != CONFIRMATION:
        raise RuntimeError(f"Reset requires --confirm {CONFIRMATION}")
    engine = create_engine(database_url)
    with Session(engine) as session:
        attempt_ids = list(session.scalars(select(Attempt.id).join(TaskVersion).join(Task).where(Task.course_id == IDS["course"]))) if args.action == "reset" else []
        removed = reset(session) if args.action == "reset" else None
        if removed is not None:
            removed["checkpoint_rows"] = reset_checkpoint(checkpoint_url, attempt_ids)
        seeded = seed(session)
    engine.dispose()
    credentials = ensure_demo_credentials()
    report = {"environment": os.getenv("TEACHING_ENV", "").upper(), "action": args.action, "namespace": "stage06-rc1", "removed": removed, "seeded": seeded, "login": {"credential_file": str(LOGIN_FILE), "student_account": credentials["student_account"], "teacher_accounts": [credentials[key] for key in ("teacher_account", "teacher2_account", "teacher3_account")]}, "passed": True}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = args.output if args.output.is_absolute() else ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
