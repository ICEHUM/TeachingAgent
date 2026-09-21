"""Export an anonymized, evidence-linked classroom pilot dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]; ROOT = BACKEND.parent; sys.path.insert(0, str(BACKEND))
from app.business.models import (
    Attempt,
    Course,
    FormalGrade,
    Intervention,
    RequirementResult,
    Snapshot,
    Submission,
    Task,
    TaskVersion,
    TeachingEvent,
)


def local_url() -> str:
    info = json.loads((Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8"))
    return f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}@{info['host']}:{info['port']}/{info['database']}"


def anon(value: str, salt: str) -> str:
    return "learner_" + hmac.new(salt.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-code", required=True)
    parser.add_argument("--database-url", default=os.getenv("TEACHING_DATABASE_URL"))
    parser.add_argument("--local-stage02b-config", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    salt = os.getenv("PILOT_EXPORT_SALT", "")
    if len(salt) < 16:
        raise RuntimeError("PILOT_EXPORT_SALT must contain at least 16 characters")
    url = local_url() if args.local_stage02b_config else args.database_url
    engine = create_engine(url); rows = []
    with Session(engine) as session:
        course = session.scalar(select(Course).where(Course.code == args.course_code))
        if course is None: raise RuntimeError("course_not_found")
        attempts = list(session.scalars(select(Attempt).join(TaskVersion).join(Task).where(Task.course_id == course.id)))
        for attempt in attempts:
            snapshots = list(session.scalars(select(Snapshot).where(Snapshot.attempt_id == attempt.id)))
            results = list(session.scalars(select(RequirementResult).where(RequirementResult.attempt_id == attempt.id)))
            events = list(session.scalars(select(TeachingEvent).where(TeachingEvent.attempt_id == attempt.id).order_by(TeachingEvent.created_at)))
            interventions = list(session.scalars(select(Intervention).where(Intervention.attempt_id == attempt.id)))
            submission = session.scalar(select(Submission).where(Submission.attempt_id == attempt.id).order_by(Submission.sequence.desc()).limit(1))
            grade = session.scalar(select(FormalGrade).where(FormalGrade.submission_id == submission.id)) if submission else None
            levels = [str((event.payload or {}).get("help_level")) for event in events if (event.payload or {}).get("help_level")]
            rows.append({
                "data_origin": "DEMO_NOT_OUTCOME" if args.course_code.startswith("DEMO-") else "PILOT",
                "learner_key": anon(attempt.learner_id, salt), "attempt_key": anon(attempt.id, salt),
                "task_version": "FAQ-001-v1", "started_at": attempt.created_at.isoformat(),
                "ended_at": events[-1].created_at.isoformat() if events and attempt.status == "completed" else "",
                "requirement_satisfied": sum(item.status == "SATISFIED" for item in results),
                "requirement_total": len(results), "guidance_levels": "|".join(levels),
                "guidance_count": len(levels), "teacher_interventions": len(interventions),
                "snapshot_count": len(snapshots), "transfer_result": "",
                "teacher_score": grade.total_score if grade else "", "student_feedback": "",
            })
    engine.dispose(); args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["data_origin", "learner_key", "attempt_key", "task_version", "started_at", "ended_at", "requirement_satisfied", "requirement_total", "guidance_levels", "guidance_count", "teacher_interventions", "snapshot_count", "transfer_result", "teacher_score", "student_feedback"]
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    print(json.dumps({"rows": len(rows), "output": str(args.output), "identifiers": "HMAC-SHA256 pseudonyms", "emails_exported": False}, ensure_ascii=False))


if __name__ == "__main__": main()
