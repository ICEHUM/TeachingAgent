"""Idempotently verify the current head against the existing non-empty PostgreSQL database."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.business.checkpoint import checkpoint_saver

OLD_ATTEMPT = "ef885ba8-cd80-4946-a7a0-65c23f303039"
OLD_INTERVENTION = "5aa75d37-349d-4ae7-ada8-dbdbea6a5287"
TARGET_REVISION = "20260921_0005"


def database_info() -> dict:
    return json.loads((Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8"))


def urls(info: dict) -> tuple[str, str, str]:
    host, port, database = info["host"], info["port"], info["database"]
    admin = f"postgresql+psycopg://postgres:{quote_plus(info['admin_password'])}@{host}:{port}/{database}"
    app = f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}@{host}:{port}/{database}"
    checkpoint = f"postgresql://langgraph_cp:{quote_plus(info['langgraph_cp_password'])}@{host}:{port}/{database}"
    return admin, app, checkpoint


def business_facts(url: str) -> dict:
    engine = create_engine(url)
    with engine.connect() as connection:
        counts = dict(connection.execute(text("""
            SELECT
              (SELECT count(*) FROM teaching_business.attempts) AS attempts,
              (SELECT count(*) FROM teaching_business.snapshots) AS snapshots,
              (SELECT count(*) FROM teaching_business.requirement_results) AS requirement_results,
              (SELECT count(*) FROM teaching_business.teaching_events) AS teaching_events,
              (SELECT count(*) FROM teaching_business.interventions) AS interventions,
              (SELECT count(*) FROM teaching_business.submissions) AS submissions,
              (SELECT count(*) FROM teaching_business.formal_grades) AS formal_grades
        """)).mappings().one())
        attempt = connection.execute(text("""
            SELECT id, state_version, status,
              (SELECT count(*) FROM teaching_business.snapshots s WHERE s.attempt_id = a.id) AS snapshots,
              (SELECT count(*) FROM teaching_business.requirement_results r WHERE r.attempt_id = a.id) AS requirement_results,
              (SELECT count(*) FROM teaching_business.teaching_events e WHERE e.attempt_id = a.id) AS teaching_events
            FROM teaching_business.attempts a WHERE id = :id
        """), {"id": OLD_ATTEMPT}).mappings().one()
        intervention = connection.execute(text("""
            SELECT id, attempt_id, status, allow_l2, requested_state_version
            FROM teaching_business.interventions WHERE id = :id
        """), {"id": OLD_INTERVENTION}).mappings().one()
        revision = connection.execute(text(
            "SELECT version_num FROM teaching_business.alembic_version"
        )).scalar_one()
        rubric_titles = list(connection.execute(text("""
            SELECT title FROM teaching_business.rubric_definitions
            WHERE item_key = 'delivery_collab'
            ORDER BY task_version_id
        """)).scalars())
        privileges = {
            table: bool(connection.execute(text(
                "SELECT has_table_privilege('teaching_app', :table, 'SELECT,INSERT,UPDATE,DELETE')"
            ), {"table": f"teaching_business.{table}"}).scalar())
            for table in ("submissions", "reviews", "review_items", "formal_grades", "rubric_definitions")
        }
    engine.dispose()
    return {
        "revision": revision,
        "delivery_rubric_titles": rubric_titles,
        "counts": counts,
        "old_attempt": dict(attempt),
        "old_intervention": dict(intervention),
        "teaching_app_crud": privileges,
    }


def latest_checkpoint_reference(url: str) -> dict:
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as connection:
        tables = list(connection.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'langgraph_checkpoint' ORDER BY table_name
        """)).scalars())
        count = int(connection.execute(text(
            "SELECT count(*) FROM langgraph_checkpoint.checkpoints"
        )).scalar_one())
        latest = connection.execute(text("""
            SELECT thread_id, checkpoint_ns, checkpoint_id
            FROM langgraph_checkpoint.checkpoints
            ORDER BY checkpoint_id DESC LIMIT 1
        """)).mappings().one()
    engine.dispose()
    return {"tables": tables, "count": count, "latest": dict(latest)}


async def restore_checkpoint(url: str, reference: dict) -> dict:
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    config = {"configurable": {
        "thread_id": reference["thread_id"],
        "checkpoint_ns": reference["checkpoint_ns"],
        "checkpoint_id": reference["checkpoint_id"],
    }}
    async with checkpoint_saver(url) as saver:
        restored = await saver.aget_tuple(config)
    if restored is None:
        return {"restored": False}
    checkpoint = restored.checkpoint
    return {
        "restored": True,
        "thread_id": restored.config["configurable"]["thread_id"],
        "checkpoint_id": restored.config["configurable"]["checkpoint_id"],
        "has_channel_values": isinstance(checkpoint.get("channel_values"), dict),
        "checkpoint_version": checkpoint.get("v"),
    }


async def main() -> None:
    info = database_info()
    admin_url, app_url, checkpoint_url = urls(info)
    before = business_facts(app_url)
    checkpoint_before = latest_checkpoint_reference(checkpoint_url)
    env = os.environ.copy()
    env["TEACHING_ALEMBIC_DATABASE_URL"] = admin_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    after = business_facts(app_url)
    checkpoint_after = latest_checkpoint_reference(checkpoint_url)
    restored = await restore_checkpoint(checkpoint_url, checkpoint_after["latest"])
    report = {
        "container": info["container"],
        "host": info["host"],
        "port": info["port"],
        "target_revision": TARGET_REVISION,
        "alembic_exit": completed.returncode,
        "alembic_stdout": completed.stdout.strip(),
        "alembic_stderr": completed.stderr.strip(),
        "before": before,
        "after": after,
        "business_counts_unchanged": before["counts"] == after["counts"],
        "existing_attempt_unchanged": before["old_attempt"] == after["old_attempt"],
        "existing_intervention_unchanged": before["old_intervention"] == after["old_intervention"],
        "checkpoint_before": checkpoint_before,
        "checkpoint_after": checkpoint_after,
        "checkpoint_restored": restored,
    }
    report["passed"] = bool(
        completed.returncode == 0
        and after["revision"] == TARGET_REVISION
        and report["business_counts_unchanged"]
        and report["existing_attempt_unchanged"]
        and report["existing_intervention_unchanged"]
        and bool(after["delivery_rubric_titles"])
        and set(after["delivery_rubric_titles"]) == {"工程规范与可复现性"}
        and all(after["teaching_app_crud"].values())
        and checkpoint_before == checkpoint_after
        and restored.get("restored")
        and restored.get("has_channel_values")
    )
    output = ROOT / "reports" / "final-ui-polish" / "migration.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main())
