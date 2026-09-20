"""Safely identify or delete Stage 05 Gate/E2E data from a local TEST database."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, delete, literal, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)

from app.business.models import (
    Attempt,
    FormalGrade,
    Intervention,
    OperationLedger,
    RequirementResult,
    Review,
    ReviewItem,
    Snapshot,
    Submission,
    TeachingEvent,
)

CONFIRMATION = "DELETE_STAGE05_TEST_DATA"
ATTEMPT_ROOT = (ROOT / "workspaces" / "attempts").resolve()
EVIDENCE_ROOT = (ROOT / "workspaces" / "evidence").resolve()


def attempt_digest(attempt_id: str) -> str:
    return hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:24]


def local_stage02b_url() -> str:
    info = json.loads(
        (Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8")
    )
    return (
        f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}"
        f"@{info['host']}:{info['port']}/{info['database']}"
    )


def assert_test_database(database_url: str) -> None:
    environment = os.getenv("TEACHING_ENV", "").upper()
    if environment != "TEST":
        raise RuntimeError("Refusing cleanup unless TEACHING_ENV=TEST")
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError("Cleanup supports the PostgreSQL integration-test database only")
    if parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Refusing cleanup for a non-loopback database host")
    database_name = (parsed.database or "").lower()
    if any(part in database_name for part in ("prod", "production")):
        raise RuntimeError("Refusing cleanup for a production-named database")


def find_candidates(session: Session, *, include_legacy: bool) -> list[str]:
    marked = set(session.scalars(
        select(Attempt.id)
        .join(OperationLedger, OperationLedger.scope == (literal("test-data:") + Attempt.id))
        .where(OperationLedger.operation_id == "stage05b:gate-e2e")
    ))
    if include_legacy:
        legacy = session.scalars(
            select(RequirementResult.attempt_id)
            .where(RequirementResult.operation_id.like("verify-b:%"))
            .distinct()
        )
        marked.update(legacy)
    return sorted(marked)


def related_counts(session: Session, attempt_ids: list[str]) -> dict[str, int]:
    if not attempt_ids:
        return {name: 0 for name in (
            "attempts", "snapshots", "submissions", "reviews", "review_items",
            "formal_grades", "requirement_results", "teaching_events", "interventions",
        )}
    submission_ids = list(session.scalars(select(Submission.id).where(Submission.attempt_id.in_(attempt_ids))))
    review_ids = list(session.scalars(select(Review.id).where(Review.submission_id.in_(submission_ids)))) if submission_ids else []
    models = {
        "attempts": (Attempt, Attempt.id.in_(attempt_ids)),
        "snapshots": (Snapshot, Snapshot.attempt_id.in_(attempt_ids)),
        "submissions": (Submission, Submission.attempt_id.in_(attempt_ids)),
        "requirement_results": (RequirementResult, RequirementResult.attempt_id.in_(attempt_ids)),
        "teaching_events": (TeachingEvent, TeachingEvent.attempt_id.in_(attempt_ids)),
        "interventions": (Intervention, Intervention.attempt_id.in_(attempt_ids)),
    }
    counts = {
        name: len(list(session.scalars(select(model.id).where(condition))))
        for name, (model, condition) in models.items()
    }
    counts["reviews"] = len(review_ids)
    counts["review_items"] = len(list(session.scalars(select(ReviewItem.id).where(ReviewItem.review_id.in_(review_ids))))) if review_ids else 0
    counts["formal_grades"] = len(list(session.scalars(select(FormalGrade.id).where(FormalGrade.submission_id.in_(submission_ids))))) if submission_ids else 0
    return counts


def delete_candidates(session: Session, attempt_ids: list[str]) -> tuple[list[str], list[str]]:
    submission_ids = list(session.scalars(select(Submission.id).where(Submission.attempt_id.in_(attempt_ids))))
    review_ids = list(session.scalars(select(Review.id).where(Review.submission_id.in_(submission_ids)))) if submission_ids else []
    if submission_ids:
        session.execute(delete(FormalGrade).where(FormalGrade.submission_id.in_(submission_ids)))
    if review_ids:
        session.execute(delete(ReviewItem).where(ReviewItem.review_id.in_(review_ids)))
        session.execute(delete(Review).where(Review.id.in_(review_ids)))
    if submission_ids:
        session.execute(delete(Submission).where(Submission.id.in_(submission_ids)))
    session.execute(delete(RequirementResult).where(RequirementResult.attempt_id.in_(attempt_ids)))
    session.execute(delete(TeachingEvent).where(TeachingEvent.attempt_id.in_(attempt_ids)))
    session.execute(delete(Intervention).where(Intervention.attempt_id.in_(attempt_ids)))
    session.execute(delete(Snapshot).where(Snapshot.attempt_id.in_(attempt_ids)))
    scopes = [
        *(f"test-data:{attempt_id}" for attempt_id in attempt_ids),
        *(f"snapshot:{attempt_id}" for attempt_id in attempt_ids),
        *(f"submission:{attempt_id}" for attempt_id in attempt_ids),
        *(f"requirement:{attempt_id}" for attempt_id in attempt_ids),
        *(f"teacher-review:{submission_id}" for submission_id in submission_ids),
        *(f"review-publish:{review_id}" for review_id in review_ids),
    ]
    if scopes:
        session.execute(delete(OperationLedger).where(OperationLedger.scope.in_(scopes)))
    session.execute(delete(Attempt).where(Attempt.id.in_(attempt_ids)))
    return submission_ids, review_ids


def remove_workspace_data(attempt_ids: list[str]) -> list[str]:
    removed: list[str] = []
    for attempt_id in attempt_ids:
        directory_name = "attempt-" + attempt_digest(attempt_id)
        attempt_dir = ATTEMPT_ROOT / directory_name
        evidence_dir = EVIDENCE_ROOT / directory_name
        for target, root in ((attempt_dir, ATTEMPT_ROOT), (evidence_dir, EVIDENCE_ROOT)):
            resolved = target.resolve()
            if root.resolve() not in resolved.parents:
                raise RuntimeError(f"Refusing to remove path outside test workspace roots: {resolved}")
            if resolved.exists():
                shutil.rmtree(resolved)
                removed.append(str(resolved.relative_to(ROOT)))
    return removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("TEACHING_DATABASE_URL"))
    parser.add_argument("--local-stage02b-config", action="store_true")
    parser.add_argument("--include-legacy-stage05b", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    database_url = local_stage02b_url() if args.local_stage02b_config else args.database_url
    if not database_url:
        raise RuntimeError("Provide --database-url or --local-stage02b-config")
    assert_test_database(database_url)
    if args.apply and args.confirm != CONFIRMATION:
        raise RuntimeError(f"Apply requires --confirm {CONFIRMATION}")
    engine = create_engine(database_url)
    with Session(engine) as session:
        candidates = find_candidates(session, include_legacy=args.include_legacy_stage05b)
        before = related_counts(session, candidates)
        removed_workspaces: list[str] = []
        if args.apply and candidates:
            delete_candidates(session, candidates)
            session.commit()
            removed_workspaces = remove_workspace_data(candidates)
        after = related_counts(session, candidates) if args.apply else before
    engine.dispose()
    report = {
        "environment": "TEST",
        "mode": "apply" if args.apply else "dry-run",
        "loopback_host": make_url(database_url).host,
        "include_legacy_stage05b": args.include_legacy_stage05b,
        "candidate_attempt_ids": candidates,
        "counts_before": before,
        "counts_after": after,
        "removed_workspace_paths": removed_workspaces,
        "passed": not args.apply or all(value == 0 for value in after.values()),
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        destination = args.output if args.output.is_absolute() else ROOT / args.output
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output, encoding="utf-8")
    sys.stdout.write(output + "\n")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
