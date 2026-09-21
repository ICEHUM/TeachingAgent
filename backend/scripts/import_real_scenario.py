"""Validate or import the Stage 06B real-source scenario into DEMO/TEST only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.business.faq import DEFAULT_TASK_POLICY
from app.business.models import (
    Attempt,
    Course,
    OperationLedger,
    RequirementDefinition,
    RubricDefinition,
    Task,
    TaskStage,
    TaskVersion,
    User,
)
from app.business.scenario_import import ScenarioPackage, load_scenario

DEFAULT_SCENARIO = ROOT / "scenarios" / "vocational-internship-policy-faq-v1"
NAMESPACE = uuid.UUID("0ba30ca2-9faf-4674-b97b-61f74b2bc849")
ATTEMPT_ROOT = (ROOT / "workspaces" / "attempts").resolve()


def stable_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def local_database_url() -> str:
    config = Path(os.environ["TEMP"]) / "teachingagent-02b-db.json"
    info = json.loads(config.read_text(encoding="utf-8"))
    return (
        f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}"
        f"@{info['host']}:{info['port']}/{info['database']}"
    )


def guard(database_url: str) -> None:
    environment = os.getenv("TEACHING_ENV", "").upper()
    if environment not in {"TEST", "DEMO"}:
        raise RuntimeError("Real scenario import requires TEACHING_ENV=TEST or DEMO")
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql" or parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Real scenario import requires loopback PostgreSQL")
    if any(marker in (parsed.database or "").lower() for marker in ("prod", "production")):
        raise RuntimeError("Refusing a production-named database")


def workspace_source(attempt_id: str) -> Path:
    digest = hashlib.sha256(attempt_id.encode()).hexdigest()[:24]
    return ATTEMPT_ROOT / f"attempt-{digest}" / "source"


def import_package(session: Session, package: ScenarioPackage) -> dict[str, object]:
    course = session.scalar(select(Course).where(Course.code == package.course["code"]))
    teacher = session.scalar(select(User).where(User.email == "teacher.rc06@demo.invalid"))
    student = session.scalar(select(User).where(User.email == "student.rc06@demo.invalid"))
    if course is None or teacher is None or student is None:
        raise RuntimeError("Run Stage 06 demo initialization before importing the real scenario")

    task = session.scalar(select(Task).where(Task.course_id == course.id, Task.task_key == package.task["key"]))
    if task is None:
        task = Task(
            id=stable_id(f"task:{package.scenario_id}"),
            course_id=course.id,
            task_key=package.task["key"],
            title=package.task["title"],
        )
        session.add(task)
    elif task.title != package.task["title"]:
        raise RuntimeError("Existing scenario task title differs; refusing to overwrite it")

    version = session.scalar(select(TaskVersion).where(TaskVersion.task_id == task.id, TaskVersion.version == package.version))
    if version is None:
        policy = dict(DEFAULT_TASK_POLICY)
        policy.update(package.policy)
        policy["scenario_id"] = package.scenario_id
        policy["stage_objectives"] = {item["key"]: item["objective"] for item in package.stages}
        policy["requirement_names"] = {
            requirement["key"]: requirement["name"]
            for stage in package.stages
            for requirement in stage["requirements"]
        }
        policy["source_manifest"] = list(package.source_manifest)
        version = TaskVersion(
            id=stable_id(f"version:{package.scenario_id}:{package.version}"),
            task_id=task.id,
            version=package.version,
            status="published",
            policy=policy,
        )
        session.add(version)
    elif (version.policy or {}).get("scenario_id") != package.scenario_id:
        raise RuntimeError("Existing task version is not owned by this scenario package")
    session.flush()

    stage_ids: dict[str, str] = {}
    for position, stage in enumerate(package.stages):
        stage_id = stable_id(f"stage:{package.scenario_id}:{stage['key']}")
        stage_ids[stage["key"]] = stage_id
        item = session.get(TaskStage, stage_id)
        if item is None:
            item = TaskStage(
                id=stage_id,
                task_version_id=version.id,
                stage_key=stage["key"],
                title=stage["title"],
                position=position,
                aggregation=stage["aggregation"],
            )
            session.add(item)
        for requirement in stage["requirements"]:
            requirement_id = stable_id(f"requirement:{package.scenario_id}:{requirement['key']}")
            if session.get(RequirementDefinition, requirement_id) is None:
                config = dict(requirement["config"])
                config["display_name"] = requirement["name"]
                session.add(RequirementDefinition(
                    id=requirement_id,
                    task_stage_id=stage_id,
                    requirement_key=requirement["key"],
                    kind=requirement["kind"],
                    required=requirement["required"],
                    version=1,
                    evaluator=requirement["evaluator"],
                    config=config,
                ))

    for position, rubric in enumerate(package.rubric):
        rubric_id = stable_id(f"rubric:{package.scenario_id}:{rubric['key']}")
        if session.get(RubricDefinition, rubric_id) is None:
            session.add(RubricDefinition(
                id=rubric_id,
                task_version_id=version.id,
                item_key=rubric["key"],
                title=rubric["title"],
                max_score=rubric["max_score"],
                position=position,
                requirement_keys=rubric["requirement_keys"],
            ))

    attempt_id = stable_id(f"attempt:{package.scenario_id}:{student.id}")
    attempt = session.get(Attempt, attempt_id)
    start_stage = str(package.policy.get("start_stage") or package.stages[0]["key"])
    if start_stage not in stage_ids:
        raise RuntimeError(f"Unknown start_stage: {start_stage}")
    if attempt is None:
        attempt = Attempt(
            id=attempt_id,
            task_version_id=version.id,
            learner_id=student.id,
            current_stage_id=stage_ids[start_stage],
            mode="guided_practice",
            status="active",
        )
        session.add(attempt)

    operation_id = f"scenario-import:{package.scenario_id}:{package.version}"
    ledger = session.scalar(select(OperationLedger).where(
        OperationLedger.scope == "scenario-import",
        OperationLedger.operation_id == operation_id,
    ))
    if ledger is None:
        session.add(OperationLedger(
            scope="scenario-import",
            operation_id=operation_id,
            status="COMPLETED",
            result_ref=f"attempt:{attempt.id}",
            result_payload={"scenario_id": package.scenario_id, "task_version_id": version.id},
        ))
    session.commit()

    source_root = workspace_source(attempt.id)
    source_root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    preserved: list[str] = []
    for relative, package_source in package.workspace_files.items():
        target = source_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            preserved.append(relative)
            continue
        target.write_text((package.root / package_source).read_text(encoding="utf-8"), encoding="utf-8")
        written.append(relative)
    return {
        "scenario_id": package.scenario_id,
        "course_id": course.id,
        "task_id": task.id,
        "task_version_id": version.id,
        "attempt_id": attempt.id,
        "start_stage": start_stage,
        "workspace": source_root.relative_to(ROOT).as_posix(),
        "written": written,
        "preserved": preserved,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "import"])
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--database-url", default=os.getenv("TEACHING_DATABASE_URL"))
    parser.add_argument("--local-stage02b-config", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    package = load_scenario(args.scenario)
    report: dict[str, object] = {
        "scenario_id": package.scenario_id,
        "task": package.task,
        "stages": len(package.stages),
        "requirements": sum(len(item["requirements"]) for item in package.stages),
        "rubric_total": sum(item["max_score"] for item in package.rubric),
        "sources": len(package.source_manifest),
        "validated": True,
    }
    if args.action == "import":
        database_url = local_database_url() if args.local_stage02b_config else args.database_url
        if not database_url:
            raise RuntimeError("Provide --database-url or --local-stage02b-config")
        guard(database_url)
        engine = create_engine(database_url)
        with Session(engine) as session:
            report["import"] = import_package(session, package)
        engine.dispose()
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = args.output if args.output.is_absolute() else ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
