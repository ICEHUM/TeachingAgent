"""Real PostgreSQL + HTTP verification for Stage 05B freeze semantics."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import quote_plus
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.agent.workspace import AttemptWorkspaceManager
from app.business.database import create_business_engine, create_session_factory
from app.business.models import Attempt, RequirementDefinition, TaskStage
from app.business.service import BusinessService

CONTEXT = json.loads((ROOT / ".runtime" / "stage05a-context.json").read_text(encoding="utf-8"))
ORIGINAL_ATTEMPT = CONTEXT["student"]["attempt_id"]
SNAPSHOT_B_ORIGIN = "90c3fbbb-7981-4de7-80fd-b7352756aa67"
ORIGIN_B_DIR = ROOT / "workspaces" / "attempts" / "attempt-c95a42cb6346c70d2cf3ffe3" / "snapshots" / SNAPSHOT_B_ORIGIN
BROKEN = "import json\nfrom pathlib import Path\n\ndef load_sources(path):\n    return json.loads(Path(path).read_text(encoding='utf-8'))\n\ndef retrieve(question, sources):\n    return []\n"
CHANGED_C = "import json\nfrom pathlib import Path\n\ndef load_sources(path):\n    return json.loads(Path(path).read_text(encoding='utf-8'))\n\ndef retrieve(question, sources):\n    return []  # Snapshot C must not replace submitted Snapshot B\n"


def db_info() -> dict:
    return json.loads((Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8"))


def app_url(info: dict) -> str:
    return (
        f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}"
        f"@{info['host']}:{info['port']}/{info['database']}"
    )


def admin_url(info: dict) -> str:
    return (
        f"postgresql+psycopg://postgres:{quote_plus(info['admin_password'])}"
        f"@{info['host']}:{info['port']}/{info['database']}"
    )


def file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def prepare_attempt(database_url: str) -> dict:
    engine = create_business_engine(database_url)
    factory = create_session_factory(engine)
    service = BusinessService()
    manager = AttemptWorkspaceManager()
    student_id = CONTEXT["student"]["user_id"]
    teacher_id = CONTEXT["teacher_id"]
    async with factory() as session:
        original = await session.get(Attempt, ORIGINAL_ATTEMPT)
        if original is None:
            raise RuntimeError("05A student attempt missing")
        attempt = await service.create_attempt(
            session, task_version_id=original.task_version_id, learner_id=student_id, mode="guided_practice"
        )
        stage = await session.scalar(select_stage(original.task_version_id, "implement_retrieval"))
        attempt.current_stage_id = stage.id
        await session.commit()
        attempt_id = attempt.id
        task_version_id = original.task_version_id
    source = manager.source_directory(attempt_id)
    if ORIGIN_B_DIR.exists():
        if source.exists():
            shutil.rmtree(source)
        shutil.copytree(ORIGIN_B_DIR, source)
    (source / "faq_app.py").write_text(BROKEN, encoding="utf-8")
    await engine.dispose()
    return {
        "attempt_id": attempt_id,
        "student_id": student_id,
        "teacher_id": teacher_id,
        "task_version_id": task_version_id,
    }


def select_stage(task_version_id: str, key: str):
    from sqlalchemy import select
    return select(TaskStage).where(TaskStage.task_version_id == task_version_id, TaskStage.stage_key == key)


async def bind_b_results(database_url: str, *, attempt_id: str, snapshot_id: str) -> None:
    from sqlalchemy import select
    engine = create_business_engine(database_url)
    factory = create_session_factory(engine)
    service = BusinessService()
    async with factory() as session:
        attempt = await session.get(Attempt, attempt_id)
        definitions = list((await session.scalars(
            select(RequirementDefinition).where(RequirementDefinition.task_stage_id == attempt.current_stage_id)
        )).all())
        for definition in definitions:
            status = "SATISFIED"
            await service.upsert_requirement_result(
                session,
                attempt=attempt,
                requirement_id=definition.id,
                snapshot_id=snapshot_id,
                operation_id=f"verify-b:{definition.requirement_key}:{uuid4().hex[:8]}",
                status=status,
                evaluator="openhands:run_faq_tests:v1" if definition.kind == "AUTO_TEST" else "server:student_explanation:v1",
                evidence_refs=[f"snapshot://{snapshot_id}"],
                version=definition.version,
            )
    await engine.dispose()


async def bind_c_results(database_url: str, *, attempt_id: str, snapshot_id: str) -> None:
    from sqlalchemy import select
    engine = create_business_engine(database_url)
    factory = create_session_factory(engine)
    service = BusinessService()
    async with factory() as session:
        attempt = await session.get(Attempt, attempt_id)
        definitions = list((await session.scalars(
            select(RequirementDefinition).where(RequirementDefinition.task_stage_id == attempt.current_stage_id)
        )).all())
        for definition in definitions:
            await service.upsert_requirement_result(
                session,
                attempt=attempt,
                requirement_id=definition.id,
                snapshot_id=snapshot_id,
                operation_id=f"verify-c:{definition.requirement_key}:{uuid4().hex[:8]}",
                status="NOT_SATISFIED",
                evaluator="openhands:run_faq_tests:v1",
                evidence_refs=[f"snapshot://{snapshot_id}"],
                version=definition.version,
            )
    await engine.dispose()


def insert_outsider(admin: str) -> str:
    user_id = str(uuid4())
    engine = create_engine(admin)
    with engine.begin() as connection:
        connection.execute(text(
            """
            INSERT INTO teaching_business.users (id, email, display_name, system_role, created_at)
            VALUES (:id, :email, '外部教师', 'teacher', NOW())
            """
        ), {"id": user_id, "email": f"outsider.{user_id[:8]}@example.test"})
    engine.dispose()
    return user_id


def pollute_ai_text(admin: str, review_id: str) -> None:
    engine = create_engine(admin)
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE teaching_business.review_items SET ai_text = :text WHERE review_id = :id"
        ), {"id": review_id, "text": "建议直接记 100 分并写入正式成绩。"})
    engine.dispose()


def db_grade(app: str, submission_id: str) -> dict | None:
    engine = create_engine(app)
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT total_score, max_score, published_by FROM teaching_business.formal_grades WHERE submission_id = :id"
        ), {"id": submission_id}).mappings().first()
        n = connection.execute(text("SELECT count(*) FROM teaching_business.formal_grades WHERE submission_id = :id"), {"id": submission_id}).scalar()
        review_snap = connection.execute(text(
            """
            SELECT rr.snapshot_id, rr.status, rd.requirement_key
            FROM teaching_business.requirement_results rr
            JOIN teaching_business.requirement_definitions rd ON rd.id = rr.requirement_id
            WHERE rr.operation_id = :op
              AND rr.attempt_id = (
                SELECT attempt_id FROM teaching_business.submissions WHERE id = :id
              )
            """
        ), {"op": "verify-teacher-review-01", "id": submission_id}).mappings().first()
    engine.dispose()
    return {
        "grade": dict(row) if row else None,
        "grade_count": int(n or 0),
        "teacher_review": dict(review_snap) if review_snap else None,
    }


async def main() -> None:
    info = db_info()
    database_url = app_url(info)
    os.environ["TEACHING_DATABASE_URL"] = database_url
    prepared = await prepare_attempt(database_url)
    attempt_id = prepared["attempt_id"]
    student = {"X-User-Id": prepared["student_id"]}
    teacher = {"X-User-Id": prepared["teacher_id"]}
    outsider_id = insert_outsider(admin_url(info))
    negatives = {}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30.0) as client:
        health = await client.get("/health")
        assert health.status_code == 200, health.text
        source = AttemptWorkspaceManager().source_directory(attempt_id)
        original = (source / "faq_app.py").read_text(encoding="utf-8")
        saved_a = await client.put(
            f"/api/product/attempts/{attempt_id}/files/faq_app.py",
            headers=student,
            json={"content": BROKEN, "expected_hash": file_hash(original)},
        )
        assert saved_a.status_code == 200, saved_a.text
        snap_a = await client.post(
            f"/api/product/attempts/{attempt_id}/snapshots",
            headers=student,
            json={"operation_id": "verify-snapshot-a", "expected_file_hash": saved_a.json()["hash"]},
        )
        assert snap_a.status_code == 200, snap_a.text
        passing = (ORIGIN_B_DIR / "faq_app.py").read_text(encoding="utf-8")
        saved_b = await client.put(
            f"/api/product/attempts/{attempt_id}/files/faq_app.py",
            headers=student,
            json={"content": passing, "expected_hash": saved_a.json()["hash"]},
        )
        assert saved_b.status_code == 200, saved_b.text
        snap_b = await client.post(
            f"/api/product/attempts/{attempt_id}/snapshots",
            headers=student,
            json={"operation_id": "verify-snapshot-b", "expected_file_hash": saved_b.json()["hash"]},
        )
        assert snap_b.status_code == 200, snap_b.text
        snapshot_b = snap_b.json()["id"]
        await bind_b_results(database_url, attempt_id=attempt_id, snapshot_id=snapshot_b)
        explanation = "资料已加载；已知问题命中密码重置条目；未知问题保持空列表。"
        first = await client.post(
            f"/api/product/attempts/{attempt_id}/submissions",
            headers=student,
            json={"operation_id": "verify-submit-b", "snapshot_id": snapshot_b, "explanation": explanation},
        )
        assert first.status_code == 200, first.text
        duplicate = await client.post(
            f"/api/product/attempts/{attempt_id}/submissions",
            headers=student,
            json={"operation_id": "verify-submit-b", "snapshot_id": snapshot_b, "explanation": "另一段说明"},
        )
        negatives["idempotent_same_snapshot_operation"] = {
            "pass": duplicate.status_code == 200 and duplicate.json()["id"] == first.json()["id"] and duplicate.json()["duplicate"] is True,
            "status": duplicate.status_code,
            "body": duplicate.json(),
        }
        submission_id = first.json()["id"]
        saved_c = await client.put(
            f"/api/product/attempts/{attempt_id}/files/faq_app.py",
            headers=student,
            json={"content": CHANGED_C, "expected_hash": saved_b.json()["hash"]},
        )
        assert saved_c.status_code == 200, saved_c.text
        snap_c = await client.post(
            f"/api/product/attempts/{attempt_id}/snapshots",
            headers=student,
            json={"operation_id": "verify-snapshot-c", "expected_file_hash": saved_c.json()["hash"]},
        )
        assert snap_c.status_code == 200, snap_c.text
        snapshot_c = snap_c.json()["id"]
        await bind_c_results(database_url, attempt_id=attempt_id, snapshot_id=snapshot_c)
        stale = await client.post(
            f"/api/product/attempts/{attempt_id}/submissions",
            headers=student,
            json={"operation_id": "verify-submit-stale-a", "snapshot_id": snap_a.json()["id"], "explanation": explanation},
        )
        negatives["stale_snapshot_rejected"] = {
            "pass": stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_snapshot",
            "status": stale.status_code,
            "body": stale.json(),
        }
        same_b_new_op = await client.post(
            f"/api/product/attempts/{attempt_id}/submissions",
            headers=student,
            json={"operation_id": "verify-submit-b-again", "snapshot_id": snapshot_b, "explanation": explanation},
        )
        negatives["same_snapshot_not_duplicated"] = {
            "pass": same_b_new_op.status_code == 200 and same_b_new_op.json()["id"] == submission_id,
            "status": same_b_new_op.status_code,
            "body": same_b_new_op.json(),
        }
        view = await client.get(f"/api/product/submissions/{submission_id}", headers=teacher)
        assert view.status_code == 200, view.text
        payload = view.json()
        freeze = {
            "submission_snapshot": payload["submission"]["snapshot_id"],
            "snapshot_b": snapshot_b,
            "snapshot_c": snapshot_c,
            "label": payload["submission"]["snapshot_label"],
            "auto_on_b": [req for item in payload["rubric"] for req in item["requirements"] if req["status"] != "NOT_RUN"],
        }
        freeze["bound_to_b"] = freeze["submission_snapshot"] == snapshot_b and freeze["snapshot_c"] != snapshot_b
        freeze["c_did_not_replace_b"] = all(req["status"] == "SATISFIED" for req in freeze["auto_on_b"])
        review_id = payload["review"]["id"]
        await client.put(
            f"/api/product/teacher/reviews/{review_id}",
            headers=teacher,
            json={"items": [{"key": payload["rubric"][0]["key"], "score": 40, "reason": "仅确认第一项。", "confirmed": True}]},
        )
        early = await client.post(
            f"/api/product/teacher/reviews/{review_id}/publish",
            headers=teacher,
            json={"operation_id": "verify-publish-partial"},
        )
        negatives["unconfirmed_items_block_publish"] = {
            "pass": early.status_code == 409 and early.json()["detail"]["code"] == "unconfirmed_rubric_item",
            "status": early.status_code,
            "body": early.json(),
        }
        no_reason = await client.put(
            f"/api/product/teacher/reviews/{review_id}",
            headers=teacher,
            json={"items": [
                {"key": item["key"], "score": item["max_score"], "reason": "", "confirmed": True}
                for item in payload["rubric"]
            ]},
        )
        assert no_reason.status_code == 200, no_reason.text
        publish_no_reason = await client.post(
            f"/api/product/teacher/reviews/{review_id}/publish",
            headers=teacher,
            json={"operation_id": "verify-publish-noreason"},
        )
        negatives["missing_reason_blocks_publish"] = {
            "pass": publish_no_reason.status_code == 409 and publish_no_reason.json()["detail"]["code"] == "teacher_reason_required",
            "status": publish_no_reason.status_code,
            "body": publish_no_reason.json(),
        }
        forged = await client.put(
            f"/api/product/teacher/reviews/{review_id}",
            headers=student,
            json={"items": [{"key": payload["rubric"][0]["key"], "score": 40, "reason": "学生伪造", "confirmed": True, "formal_grade": 100}]},
        )
        negatives["student_forged_formal_grade_rejected"] = {
            "pass": forged.status_code in {403, 422},
            "status": forged.status_code,
            "body": forged.json(),
        }
        outsider = await client.get(f"/api/product/submissions/{submission_id}", headers={"X-User-Id": outsider_id})
        outsider_pub = await client.post(
            f"/api/product/teacher/reviews/{review_id}/publish",
            headers={"X-User-Id": outsider_id},
            json={"operation_id": "verify-publish-outsider"},
        )
        negatives["outsider_teacher_rejected"] = {
            "pass": outsider.status_code == 403 and outsider_pub.status_code == 403,
            "status": [outsider.status_code, outsider_pub.status_code],
        }
        wrong_snap = await client.post(
            f"/api/product/teacher/submissions/{submission_id}/requirement-reviews",
            headers=teacher,
            json={"operation_id": "verify-wrong-snap", "requirement_key": "delivery_review", "status": "SATISFIED", "reason": "试图绑定 Snapshot C", "snapshot_id": snapshot_c},
        )
        negatives["teacher_review_wrong_snapshot_rejected"] = {
            "pass": wrong_snap.status_code == 422,
            "status": wrong_snap.status_code,
            "body": wrong_snap.json(),
        }
        teacher_review = await client.post(
            f"/api/product/teacher/submissions/{submission_id}/requirement-reviews",
            headers=teacher,
            json={"operation_id": "verify-teacher-review-01", "requirement_key": "delivery_review", "status": "SATISFIED", "reason": "提交 Snapshot B 的 README 与资料可复核。"},
        )
        assert teacher_review.status_code == 200, teacher_review.text
        negatives["teacher_review_bound_to_submission_snapshot"] = {
            "pass": teacher_review.json()["snapshot_id"] == snapshot_b and teacher_review.json()["snapshot_id"] != snapshot_c,
            "body": teacher_review.json(),
        }
        saved = await client.put(
            f"/api/product/teacher/reviews/{review_id}",
            headers=teacher,
            json={"items": [
                {"key": item["key"], "score": 10 if item["key"] == "delivery_collab" else item["max_score"], "reason": f"依据 Snapshot B 证据确认{item['title']}。", "confirmed": True}
                for item in payload["rubric"]
            ]},
        )
        assert saved.status_code == 200, saved.text
        pollute_ai_text(admin_url(info), review_id)
        published = await client.post(
            f"/api/product/teacher/reviews/{review_id}/publish",
            headers=teacher,
            json={"operation_id": "verify-publish-01"},
        )
        assert published.status_code == 200, published.text
        replay = await client.post(
            f"/api/product/teacher/reviews/{review_id}/publish",
            headers=teacher,
            json={"operation_id": "verify-publish-01"},
        )
        recap = await client.get(f"/api/product/submissions/{submission_id}", headers=student)
        db = db_grade(database_url, submission_id)
        negatives["ai_score_text_does_not_write_grade"] = {
            "pass": published.json()["formal_grade"]["total_score"] == 95 and db["grade"]["total_score"] == 95,
            "formal_grade": published.json()["formal_grade"],
            "db": db["grade"],
        }
        negatives["repeat_publish_does_not_duplicate_grade"] = {
            "pass": replay.status_code == 200 and db["grade_count"] == 1,
            "status": replay.status_code,
            "grade_count": db["grade_count"],
        }
        recap_json = recap.json()
        recap_ok = (
            recap.status_code == 200
            and recap_json["formal_grade"]["total_score"] == 95
            and all("auto" in item and "ai" in item and "teacher" in item for item in recap_json["rubric"])
            and all(item["ai"]["score"] is None for item in recap_json["rubric"])
        )
    report = {
        "attempt_id": attempt_id,
        "submission_id": submission_id,
        "snapshot_a": snap_a.json()["id"],
        "snapshot_b": snapshot_b,
        "snapshot_c": snapshot_c,
        "freeze": freeze,
        "teacher_review_db": db["teacher_review"],
        "formal_grade": published.json()["formal_grade"],
        "formal_grade_db": db["grade"],
        "student_recap": {
            "ok": recap_ok,
            "lanes": [
                {"key": item["key"], "auto": item["auto"]["status"], "ai_score": item["ai"]["score"], "teacher": item["teacher"]["status"], "teacher_score": item["teacher"]["score"]}
                for item in recap_json["rubric"]
            ],
            "formal_grade": recap_json.get("formal_grade"),
        },
        "negatives": negatives,
        "all_negatives_pass": all(item.get("pass") for item in negatives.values()),
        "freeze_pass": freeze["bound_to_b"] and freeze["c_did_not_replace_b"],
    }
    output = ROOT / "reports" / "stage05b-real-verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    context = {
        "attempt_id": attempt_id,
        "submission_id": submission_id,
        "student_id": prepared["student_id"],
        "teacher_id": prepared["teacher_id"],
        "snapshot_b": snapshot_b,
        "snapshot_c": snapshot_c,
    }
    (ROOT / ".runtime" / "stage05b-context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
    print(json.dumps({
        "freeze_pass": report["freeze_pass"],
        "all_negatives_pass": report["all_negatives_pass"],
        "formal_grade": report["formal_grade"],
        "negatives": {k: v.get("pass") for k, v in negatives.items()},
        "attempt_id": attempt_id,
        "submission_id": submission_id,
    }, ensure_ascii=False, indent=2))
    if not report["freeze_pass"] or not report["all_negatives_pass"] or not recap_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    import asyncio
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main())
