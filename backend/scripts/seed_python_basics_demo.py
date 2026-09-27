"""Seed the PYB-01 example task in a loopback DEMO/TEST database only."""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from stage06_demo import IDS as LEGACY_IDS
from stage06_demo import guard, local_urls

from app.agent.workspace import AttemptWorkspaceManager
from app.business.models import (
    Attempt,
    Course,
    CourseMembership,
    RequirementDefinition,
    RubricDefinition,
    Task,
    TaskStage,
    TaskVersion,
    User,
)
from app.business.python_basics import (
    COURSE_CODE,
    FILE_NAME,
    OBJECTIVE,
    REQUIREMENTS,
    RUBRIC,
    STAGE_KEY,
    STARTER,
    TASK_KEY,
    TASK_PACKS,
    TITLE,
    VERSION,
)

NAMESPACE = uuid.UUID("a12a8cb5-9433-4246-9509-e545cf3dad8f")


def stable_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def seed(session: Session) -> dict[str, str]:
    teacher = session.get(User, LEGACY_IDS["teacher"])
    student = session.get(User, LEGACY_IDS["student"])
    if teacher is None or student is None:
        raise RuntimeError("Initialize Stage 06 demo identities before seeding PYB-01")
    course = session.get(Course, stable_id("course"))
    if course is None:
        course = Course(id=stable_id("course"), code=COURSE_CODE, name="Python 基础编程（示例课程）")
        session.add(course)
    for key, user, role in (
        ("teacher", teacher, "teacher"),
        ("teacher2", session.get(User, LEGACY_IDS["teacher2"]), "teacher"),
        ("teacher3", session.get(User, LEGACY_IDS["teacher3"]), "teacher"),
        ("student", student, "student"),
    ):
        if user is not None and session.get(CourseMembership, stable_id(f"membership-{key}")) is None:
            session.add(CourseMembership(id=stable_id(f"membership-{key}"),
                                         course_id=course.id, user_id=user.id, role=role))
    task = session.get(Task, stable_id("task"))
    if task is None:
        task = Task(id=stable_id("task"), course_id=course.id, task_key=TASK_KEY, title=TITLE)
        session.add(task)
    version = session.get(TaskVersion, stable_id("version"))
    if version is None:
        version = TaskVersion(id=stable_id("version"), task_id=task.id,
                              version=VERSION, status="published", policy={
                                  "demo_namespace": "python-basics-example-v1",
                                  "stage_objectives": {STAGE_KEY: OBJECTIVE},
                                  "start_stage": STAGE_KEY,
                              })
        session.add(version)
    stage = session.get(TaskStage, stable_id("stage"))
    if stage is None:
        stage = TaskStage(id=stable_id("stage"), task_version_id=version.id,
                          stage_key=STAGE_KEY, title="编写与验证", position=0,
                          aggregation="ALL_REQUIRED")
        session.add(stage)
    for key, name, kind, evaluator, tool in REQUIREMENTS:
        if session.get(RequirementDefinition, stable_id(f"requirement-{key}")) is None:
            session.add(RequirementDefinition(
                id=stable_id(f"requirement-{key}"), task_stage_id=stage.id,
                requirement_key=key, kind=kind, required=True, version=1,
                evaluator=evaluator, config={"display_name": name, "tool": tool},
            ))
    for position, (key, title, score, requirement_keys) in enumerate(RUBRIC):
        if session.get(RubricDefinition, stable_id(f"rubric-{key}")) is None:
            session.add(RubricDefinition(
                id=stable_id(f"rubric-{key}"), task_version_id=version.id,
                item_key=key, title=title, max_score=score,
                position=position, requirement_keys=list(requirement_keys),
            ))
    attempt = session.get(Attempt, stable_id("attempt"))
    if attempt is None:
        attempt = Attempt(id=stable_id("attempt"), task_version_id=version.id,
                          learner_id=student.id, current_stage_id=stage.id,
                          mode="guided_practice", status="active")
        session.add(attempt)
    session.commit()

    manager = AttemptWorkspaceManager()
    source = manager.source_directory(attempt.id)
    source.mkdir(parents=True, exist_ok=True)
    starter = source / FILE_NAME
    if not starter.exists():
        starter.write_text(STARTER, encoding="utf-8")
    readme = source / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {TITLE}\n\n{OBJECTIVE}\n\n在 `{FILE_NAME}` 中完成程序。"
            "先运行公开样例检查，再提交当前代码版本。\n",
            encoding="utf-8",
        )
    for pack in TASK_PACKS.values():
        if pack.key == TASK_KEY:
            continue
        task_id = stable_id(f"task-{pack.key}")
        version_id = stable_id(f"version-{pack.key}")
        stage_id = stable_id(f"stage-{pack.key}")
        attempt_id = stable_id(f"attempt-{pack.key}")
        if session.get(Task, task_id) is None:
            session.add(Task(id=task_id, course_id=course.id, task_key=pack.key, title=pack.title))
        if session.get(TaskVersion, version_id) is None:
            session.add(TaskVersion(
                id=version_id, task_id=task_id, version=VERSION, status="published",
                policy={"demo_namespace": "python-basics-example-v1",
                        "stage_objectives": {STAGE_KEY: pack.objective}, "start_stage": STAGE_KEY},
            ))
        if session.get(TaskStage, stage_id) is None:
            session.add(TaskStage(id=stage_id, task_version_id=version_id,
                                  stage_key=STAGE_KEY, title="编写与验证", position=0,
                                  aggregation="ALL_REQUIRED"))
        for key, name, kind, evaluator, tool in pack.requirements:
            requirement_id = stable_id(f"requirement-{pack.key}-{key}")
            if session.get(RequirementDefinition, requirement_id) is None:
                session.add(RequirementDefinition(
                    id=requirement_id, task_stage_id=stage_id,
                    requirement_key=key, kind=kind, required=True, version=1,
                    evaluator=evaluator, config={"display_name": name, "tool": tool},
                ))
        for position, (key, title, score, requirement_keys) in enumerate(pack.rubric):
            rubric_id = stable_id(f"rubric-{pack.key}-{key}")
            if session.get(RubricDefinition, rubric_id) is None:
                session.add(RubricDefinition(
                    id=rubric_id, task_version_id=version_id, item_key=key,
                    title=title, max_score=score, position=position,
                    requirement_keys=list(requirement_keys),
                ))
        if session.get(Attempt, attempt_id) is None:
            session.add(Attempt(id=attempt_id, task_version_id=version_id,
                                learner_id=student.id, current_stage_id=stage_id,
                                mode="guided_practice", status="active"))
        session.commit()
        extra_source = manager.source_directory(attempt_id)
        extra_source.mkdir(parents=True, exist_ok=True)
        if not (extra_source / FILE_NAME).exists():
            (extra_source / FILE_NAME).write_text(pack.starter, encoding="utf-8")
        if not (extra_source / "README.md").exists():
            (extra_source / "README.md").write_text(
                f"# {pack.title}\n\n{pack.objective}\n\n在 `{FILE_NAME}` 中完成程序。"
                "先运行公开样例检查，再提交当前代码版本。\n", encoding="utf-8",
            )
    return {"course_code": course.code, "task": f"{TASK_KEY}-{VERSION}",
            "attempt_id": attempt.id, "workspace": str(source),
            "extra_attempts": {key: stable_id(f"attempt-{key}") for key in ("PYB-02", "PYB-03")}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("TEACHING_DATABASE_URL"))
    parser.add_argument("--local-stage02b-config", action="store_true")
    args = parser.parse_args()
    database_url = local_urls()[0] if args.local_stage02b_config else args.database_url
    if not database_url:
        raise RuntimeError("Provide --database-url or --local-stage02b-config")
    guard(database_url)
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            print(seed(session))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
