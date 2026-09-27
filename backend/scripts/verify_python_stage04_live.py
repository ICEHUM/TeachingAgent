"""Verify PYB-02/03 through the local DEMO API and real Docker Runner."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from seed_python_basics_demo import stable_id as pack_id
from stage06_demo import IDS, guard, local_urls

from app.agent.workspace import AttemptWorkspaceManager
from app.business.models import Attempt, CourseMembership, User
from app.business.python_basics import TASK_PACKS

logging.disable(logging.CRITICAL)
NAMESPACE = uuid.UUID("ed5996c0-d346-47ae-9f16-7a5fb5249127")
SAMPLES = {
    "passing_correct": ("PYB-02", "判断正确", "score=int(input())\nif score >= 60:\n    print('及格')\nelse:\n    print('不及格')\n", "SATISFIED", "SATISFIED"),
    "passing_boundary": ("PYB-02", "100 分边界漏判", "score=int(input())\nif 60 <= score < 100:\n    print('及格')\nelse:\n    print('不及格')\n", "SATISFIED", "NOT_SATISFIED"),
    "passing_public": ("PYB-02", "60 分边界错误", "score=int(input())\nif score > 60:\n    print('及格')\nelse:\n    print('不及格')\n", "NOT_SATISFIED", None),
    "traversal_correct": ("PYB-03", "遍历正确", "count=int(input())\nnumbers=[int(input()) for _ in range(count)]\ntotal=0\nfor number in numbers:\n    total+=number\nprint(total)\n", "SATISFIED", "SATISFIED"),
    "traversal_duplicate": ("PYB-03", "重复值漏算", "count=int(input())\nnumbers=[int(input()) for _ in range(count)]\ntotal=0\nseen=set()\nfor number in numbers:\n    if number not in seen:\n        total+=number\n        seen.add(number)\nprint(total)\n", "SATISFIED", "NOT_SATISFIED"),
    "traversal_structure": ("PYB-03", "未使用 for 遍历", "count=int(input())\nnumbers=[int(input()) for _ in range(count)]\nprint(sum(numbers))\n", "NOT_SATISFIED", None),
}


def stable_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def api(client: httpx.Client, method: str, path: str, user: str,
        *, payload: dict | None = None, status: int = 200) -> dict:
    response = client.request(method, path, headers={"X-User-Id": user}, json=payload)
    if response.status_code != status:
        raise AssertionError(f"{method} {path}: HTTP {response.status_code}, {response.text[:800]}")
    return response.json()


def seed(database_url: str) -> dict[str, tuple[str, str]]:
    guard(database_url)
    engine = create_engine(database_url)
    identities = {}
    try:
        with Session(engine) as session:
            for key, (task_key, label, _source, _public, _hidden) in SAMPLES.items():
                user_id, attempt_id = stable_id(f"user-{key}"), stable_id(f"attempt-{key}")
                existing = session.get(Attempt, attempt_id)
                if existing is None:
                    session.add(User(id=user_id, email=f"stage04-{key}@demo.invalid",
                                     display_name=f"阶段04验收｜{label}", system_role="student"))
                    session.flush()
                    session.add(CourseMembership(
                        id=stable_id(f"membership-{key}"), course_id=pack_id("course"),
                        user_id=user_id, role="student",
                    ))
                    session.add(Attempt(
                        id=attempt_id, task_version_id=pack_id(f"version-{task_key}"),
                        learner_id=user_id, current_stage_id=pack_id(f"stage-{task_key}"),
                        mode="guided_practice", status="active",
                    ))
                elif existing.learner_id != user_id or existing.task_version_id != pack_id(f"version-{task_key}"):
                    raise RuntimeError(f"Unexpected acceptance attempt ownership: {key}")
                identities[key] = (user_id, attempt_id)
            session.commit()
        manager = AttemptWorkspaceManager()
        for key, (_user, attempt_id) in identities.items():
            starter = manager.source_directory(attempt_id) / "main.py"
            if not starter.exists():
                starter.write_text(TASK_PACKS[SAMPLES[key][0]].starter, encoding="utf-8")
    finally:
        engine.dispose()
    return identities


def verify(client: httpx.Client, key: str, user: str, attempt: str) -> dict:
    task_key, _label, source, public_expected, hidden_expected = SAMPLES[key]
    pack = TASK_PACKS[task_key]
    base = f"/api/product/attempts/{attempt}"
    workbench = api(client, "GET", base + "/workbench", user)
    assert workbench["task"]["key"] == task_key
    snapshot = workbench["latest_snapshot"]
    if snapshot is None:
        current = api(client, "GET", base + "/files/main.py", user)
        saved = api(client, "PUT", base + "/files/main.py", user,
                    payload={"content": source, "expected_hash": current["hash"]})
        snapshot = api(client, "POST", base + "/snapshots", user,
                       payload={"operation_id": f"stage04-snapshot-{key}",
                                "expected_file_hash": saved["hash"], "expected_file_path": "main.py"})
    snapshot_id = snapshot["id"]
    workbench = api(client, "GET", base + "/workbench", user)
    results = {item["key"]: item for item in workbench["requirements"]}
    if results[pack.public_requirement]["status"] != public_expected:
        run = api(client, "POST", base + "/runs", user,
                  payload={"operation_id": f"stage04-public-{key}-{uuid.uuid4().hex[:8]}",
                           "snapshot_id": snapshot_id,
                           "expected_state_version": workbench["attempt"]["state_version"]})
        assert run["last_tool_status"] == ("succeeded" if public_expected == "SATISFIED" else "student_failure"), (key, run)
        workbench = api(client, "GET", base + "/workbench", user)
        results = {item["key"]: item for item in workbench["requirements"]}
    assert results[pack.public_requirement]["status"] == public_expected
    public_detail = api(client, "GET", base + "/evidence/" + results[pack.public_requirement]["operation_id"], user)
    assert public_detail["snapshot"] == snapshot["label"]
    assert public_detail["skill_evidence"]
    if key == "passing_public":
        assert any(item["diagnosis_code"] == "boundary_condition" for item in public_detail["checks"])
    if key == "traversal_structure":
        assert any(item["diagnosis_code"] == "missing_for_traversal" for item in public_detail["checks"])
    if hidden_expected is not None:
        if results[pack.hidden_requirement]["status"] != hidden_expected:
            response = api(client, "POST", base + "/submissions", user,
                           payload={"operation_id": f"stage04-submit-{key}",
                                    "snapshot_id": snapshot_id,
                                    "explanation": "阶段 04 本地真实容器验收样本。"},
                           status=200 if hidden_expected == "SATISFIED" else 409)
            if hidden_expected == "NOT_SATISFIED":
                assert response["detail"]["code"] == "hidden_check_failed"
        workbench = api(client, "GET", base + "/workbench", user)
        results = {item["key"]: item for item in workbench["requirements"]}
        assert results[pack.hidden_requirement]["status"] == hidden_expected
        assert bool(workbench["latest_submission"]) == (hidden_expected == "SATISFIED")
        hidden_detail = api(client, "GET", base + "/evidence/" + results[pack.hidden_requirement]["operation_id"], user)
        assert [item["code"] for item in hidden_detail["checks"]] == ["hidden_suite"]
        assert hidden_detail["skill_evidence"] == []
    teacher = api(client, "GET", f"/api/product/teacher/attempts/{attempt}", IDS["teacher"])
    teacher_results = {item["key"]: item["status"] for item in teacher["requirements"]}
    assert teacher_results == {key: item["status"] for key, item in results.items()}
    return {"task": task_key, "public": public_expected, "hidden": hidden_expected,
            "submission": bool(workbench["latest_submission"]), "teacher_evidence": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-stage02b-config", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if not args.local_stage02b_config or args.base_url not in {"http://127.0.0.1:8000", "http://localhost:8000"}:
        raise RuntimeError("Use the explicit local DEMO config and port 8000")
    identities = seed(local_urls()[0])
    with httpx.Client(base_url=args.base_url, timeout=180, trust_env=False) as client:
        results = {key: verify(client, key, *identities[key]) for key in SAMPLES}
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
