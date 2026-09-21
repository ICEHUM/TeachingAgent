"""Initialize or reset the isolated Stage 06 FAQ demonstration namespace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote_plus

import psycopg
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

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


def ensure_demo_credentials() -> dict[str, str]:
    if LOGIN_FILE.exists():
        payload = json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
        required = {"student_account", "student_password", "teacher_account", "teacher_password"}
        if required.issubset(payload) and all(payload[key] for key in required):
            return payload
    payload = {
        "student_account": "demo_student",
        "student_password": secrets.token_urlsafe(12),
        "teacher_account": "demo_teacher",
        "teacher_password": secrets.token_urlsafe(12),
    }
    LOGIN_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload




def stable_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


IDS = {
    "teacher": stable_id("teacher"), "student": stable_id("student"),
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


def seed(session: Session) -> dict[str, str]:
    teacher = session.get(User, IDS["teacher"])
    if teacher is None:
        teacher = User(id=IDS["teacher"], email="teacher.rc06@demo.invalid", display_name="演示教师", system_role="teacher")
        session.add(teacher)
    student = session.get(User, IDS["student"])
    if student is None:
        student = User(id=IDS["student"], email="student.rc06@demo.invalid", display_name="演示学生", system_role="student")
        session.add(student)
    course = session.get(Course, IDS["course"])
    if course is None:
        course = Course(id=IDS["course"], code=COURSE_CODE, name="AI 应用开发实训 · Stage 06 演示班")
        session.add(course)
    for user_id, role, suffix in ((IDS["teacher"], "teacher", "teacher"), (IDS["student"], "student", "student")):
        membership_id = stable_id("membership-" + suffix)
        if session.get(CourseMembership, membership_id) is None:
            session.add(CourseMembership(id=membership_id, course_id=course.id, user_id=user_id, role=role))
    task = session.get(Task, IDS["task"])
    if task is None:
        task = Task(id=IDS["task"], course_id=course.id, task_key="FAQ-001", title="带来源引用的 FAQ 问答服务")
        session.add(task)
    version = session.get(TaskVersion, IDS["version"])
    if version is None:
        policy = dict(DEFAULT_TASK_POLICY)
        policy["demo_namespace"] = "stage06-rc1"
        version = TaskVersion(id=IDS["version"], task_id=task.id, version="v1", status="published", policy=policy)
        session.add(version)
    session.flush()
    stage_ids: list[str] = []
    for position, (stage_key, title, requirements) in enumerate(FAQ_STAGES):
        stage_id = stable_id("stage-" + stage_key)
        stage_ids.append(stage_id)
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
        attempt = Attempt(id=IDS["attempt"], task_version_id=version.id, learner_id=student.id, current_stage_id=stage_ids[0], mode="guided_practice", status="active")
        session.add(attempt)
    ledger = session.scalar(select(OperationLedger).where(OperationLedger.scope == "demo-namespace", OperationLedger.operation_id == "stage06:rc1"))
    if ledger is None:
        session.add(OperationLedger(scope="demo-namespace", operation_id="stage06:rc1", status="COMPLETED", result_ref=f"attempt:{attempt.id}", result_payload={"course_code": COURSE_CODE, "namespace": "stage06-rc1"}))
    session.commit()
    source = workspace_source()
    source.mkdir(parents=True, exist_ok=True)
    files = {
        "app.py": """import json\nfrom pathlib import Path\n\nDATA = json.loads((Path(__file__).parent / "faq_data.json").read_text(encoding="utf-8"))\n\ndef retrieve(question: str):\n    # Stage 06 demo starts with a deliberate student bug: loaded data is never matched.\n    return None\n\ndef answer(question: str):\n    item = retrieve(question)\n    if item is None:\n        return {"answer": "暂未找到答案", "source": None}\n    return {"answer": item["answer"], "source": item["source"]}\n""",
        "faq_data.json": json.dumps([
            {"question": "实训室开放时间是什么？", "answer": "工作日 8:30—17:30。", "source": "https://school.example.invalid/lab-hours"},
            {"question": "如何预约 GPU？", "answer": "在课程平台提交预约申请。", "source": "https://school.example.invalid/gpu-booking"},
            {"question": "作业如何提交？", "answer": "创建 Snapshot 后提交指定版本。", "source": "https://school.example.invalid/submission"},
        ], ensure_ascii=False, indent=2),
        "README.md": "# FAQ-001-v1 演示任务\n\n运行检查会验证已知问题命中、未知问题边界和来源引用。请由学生自行定位并修改 `app.py`。\n",
    }
    for name, content in files.items():
        target = source / name
        if not target.exists():
            target.write_text(content, encoding="utf-8")
    return {**IDS, "course_code": COURSE_CODE, "workspace": "isolated-demo-workspace"}


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
    report = {"environment": os.getenv("TEACHING_ENV", "").upper(), "action": args.action, "namespace": "stage06-rc1", "removed": removed, "seeded": seeded, "login": {"credential_file": str(LOGIN_FILE), "student_account": credentials["student_account"], "teacher_account": credentials["teacher_account"]}, "passed": True}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = args.output if args.output.is_absolute() else ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
