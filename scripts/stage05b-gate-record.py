"""Export non-secret PostgreSQL evidence for the final Stage 05B browser run."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    db = json.loads((Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8"))
    context = json.loads((ROOT / ".runtime" / "stage05b-context.json").read_text(encoding="utf-8"))
    url = (
        f"postgresql+psycopg://teaching_app:{quote_plus(db['teaching_app_password'])}"
        f"@{db['host']}:{db['port']}/{db['database']}"
    )
    engine = create_engine(url)
    with engine.connect() as connection:
        submission = dict(connection.execute(text("""
            SELECT s.id, s.attempt_id, s.snapshot_id, s.operation_id, s.sequence, s.status,
                   s.created_at, snap.sequence AS snapshot_sequence
            FROM teaching_business.submissions s
            JOIN teaching_business.snapshots snap ON snap.id = s.snapshot_id
            WHERE s.id = :submission_id
        """), {"submission_id": context["submission_id"]}).mappings().one())
        latest_snapshot = dict(connection.execute(text("""
            SELECT id, sequence, created_at FROM teaching_business.snapshots
            WHERE attempt_id = :attempt_id ORDER BY sequence DESC LIMIT 1
        """), {"attempt_id": context["attempt_id"]}).mappings().one())
        review = dict(connection.execute(text("""
            SELECT id, submission_id, teacher_id, status, publish_operation_id, published_at
            FROM teaching_business.reviews WHERE submission_id = :submission_id
        """), {"submission_id": context["submission_id"]}).mappings().one())
        review_items = [dict(row) for row in connection.execute(text("""
            SELECT rubric_key, auto_status, teacher_score, teacher_reason, teacher_confirmed
            FROM teaching_business.review_items WHERE review_id = :review_id ORDER BY rubric_key
        """), {"review_id": review["id"]}).mappings()]
        grade = dict(connection.execute(text("""
            SELECT id, submission_id, review_id, total_score, max_score, published_by,
                   published_at, change_reason
            FROM teaching_business.formal_grades WHERE submission_id = :submission_id
        """), {"submission_id": context["submission_id"]}).mappings().one())
        requirements = [dict(row) for row in connection.execute(text("""
            SELECT rd.requirement_key, rd.kind, rr.snapshot_id, rr.operation_id, rr.status,
                   rr.evaluator, rr.evidence_refs, rr.evaluated_at
            FROM teaching_business.requirement_results rr
            JOIN teaching_business.requirement_definitions rd ON rd.id = rr.requirement_id
            WHERE rr.attempt_id = :attempt_id
            ORDER BY rr.evaluated_at
        """), {"attempt_id": context["attempt_id"]}).mappings()]
        grade_count = int(connection.execute(text("""
            SELECT count(*) FROM teaching_business.formal_grades WHERE submission_id = :submission_id
        """), {"submission_id": context["submission_id"]}).scalar_one())

    engine.dispose()
    b_results = [item for item in requirements if item["snapshot_id"] == context["snapshot_b"]]
    c_results = [item for item in requirements if item["snapshot_id"] == context["snapshot_c"]]
    teacher_review_results = [item for item in b_results if item["kind"] == "TEACHER_REVIEW"]
    report = {
        "submission": submission,
        "expected_snapshot_b": context["snapshot_b"],
        "latest_snapshot": latest_snapshot,
        "expected_snapshot_c": context["snapshot_c"],
        "frozen_to_b_while_latest_is_c": (
            submission["snapshot_id"] == context["snapshot_b"]
            and latest_snapshot["id"] == context["snapshot_c"]
        ),
        "review": review,
        "review_items": review_items,
        "formal_grade": grade,
        "formal_grade_count": grade_count,
        "snapshot_b_requirement_results": b_results,
        "snapshot_c_requirement_results": c_results,
        "teacher_review_results": teacher_review_results,
        "passed": bool(
            submission["snapshot_id"] == context["snapshot_b"]
            and latest_snapshot["id"] == context["snapshot_c"]
            and review["status"] == "published"
            and len(review_items) == 4
            and all(item["teacher_confirmed"] and item["teacher_reason"] for item in review_items)
            and grade["total_score"] == 95
            and grade_count == 1
            and teacher_review_results
            and all(item["snapshot_id"] == context["snapshot_b"] for item in teacher_review_results)
        ),
    }
    output = ROOT / "reports" / "stage05b-gate-record.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2, default=str))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
