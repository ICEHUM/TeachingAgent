"""Upgrade the existing Stage 05A PostgreSQL instance and assert old facts remain."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, text


def load_db() -> dict:
    path = Path(os.environ["TEMP"]) / "teachingagent-02b-db.json"
    return json.loads(path.read_text(encoding="utf-8"))


def urls(info: dict) -> tuple[str, str, str]:
    host, port, database = info["host"], info["port"], info["database"]
    admin = f"postgresql+psycopg://postgres:{quote_plus(info['admin_password'])}@{host}:{port}/{database}"
    app = f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}@{host}:{port}/{database}"
    checkpoint = f"postgresql://langgraph_cp:{quote_plus(info['langgraph_cp_password'])}@{host}:{port}/{database}"
    return admin, app, checkpoint


COUNTS_SQL = """
SELECT
  (SELECT count(*) FROM teaching_business.attempts) AS attempts,
  (SELECT count(*) FROM teaching_business.snapshots) AS snapshots,
  (SELECT count(*) FROM teaching_business.requirement_results) AS requirement_results,
  (SELECT count(*) FROM teaching_business.teaching_events) AS teaching_events,
  (SELECT count(*) FROM teaching_business.interventions) AS interventions
"""


def fetch_counts(url: str) -> dict:
    engine = create_engine(url)
    with engine.connect() as connection:
        row = connection.execute(text(COUNTS_SQL)).mappings().one()
        version = connection.execute(text("SELECT version_num FROM teaching_business.alembic_version")).scalar()
        attempt = connection.execute(
            text("SELECT id, state_version, status FROM teaching_business.attempts WHERE id = :id"),
            {"id": "ef885ba8-cd80-4946-a7a0-65c23f303039"},
        ).mappings().first()
        snapshots = list(connection.execute(
            text("SELECT id, sequence FROM teaching_business.snapshots WHERE attempt_id = :id ORDER BY sequence"),
            {"id": "ef885ba8-cd80-4946-a7a0-65c23f303039"},
        ).mappings())
        intervention = connection.execute(
            text("SELECT id, status, allow_l2 FROM teaching_business.interventions WHERE id = :id"),
            {"id": "5aa75d37-349d-4ae7-ada8-dbdbea6a5287"},
        ).mappings().first()
        checkpoint_n = connection.execute(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'langgraph_checkpoint'"
        )).scalar()
    engine.dispose()
    return {
        "alembic_version": version,
        "counts": dict(row),
        "student_attempt": dict(attempt) if attempt else None,
        "student_snapshots": [dict(item) for item in snapshots],
        "teacher_intervention": dict(intervention) if intervention else None,
        "checkpoint_tables": int(checkpoint_n or 0),
    }


def main() -> None:
    info = load_db()
    admin_url, app_url, checkpoint_url = urls(info)
    before = fetch_counts(app_url)
    env = os.environ.copy()
    env["TEACHING_ALEMBIC_DATABASE_URL"] = admin_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    after = fetch_counts(app_url)
    engine = create_engine(app_url)
    with engine.connect() as connection:
        new_tables = list(connection.execute(text(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'teaching_business'
              AND table_name IN ('submissions','reviews','review_items','formal_grades','rubric_definitions')
            ORDER BY table_name
            """
        )).scalars())
        can_insert = connection.execute(text(
            "SELECT has_table_privilege('teaching_app', 'teaching_business.submissions', 'INSERT')"
        )).scalar()
    engine.dispose()
    report = {
        "container": info["container"],
        "port": info["port"],
        "alembic_exit": completed.returncode,
        "alembic_stdout": completed.stdout.strip(),
        "alembic_stderr": completed.stderr.strip(),
        "before": before,
        "after": after,
        "new_tables": new_tables,
        "teaching_app_can_insert_submissions": bool(can_insert),
        "checkpoint_url_configured": bool(checkpoint_url),
        "old_facts_unchanged": (
            before["counts"] == after["counts"]
            and before["student_attempt"] == after["student_attempt"]
            and before["student_snapshots"] == after["student_snapshots"]
            and before["teacher_intervention"] == after["teacher_intervention"]
            and after["alembic_version"] == "20260920_0004"
            and before["alembic_version"] != "20260920_0004"
        ),
    }
    output = ROOT / "reports" / "stage05b-migration.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "alembic_exit", "before", "after", "new_tables",
        "teaching_app_can_insert_submissions", "old_facts_unchanged",
    )}, ensure_ascii=False, indent=2))
    if completed.returncode != 0 or not report["old_facts_unchanged"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
