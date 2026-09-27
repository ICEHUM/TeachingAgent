"""Exercise PYB-01 through the live DEMO API and real Docker workspaces.

The two named acceptance attempts are retained as clearly marked demo evidence.
This script refuses to touch them again after they have snapshots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

logging.disable(logging.CRITICAL)

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from seed_python_basics_demo import stable_id as task_id
from stage06_demo import IDS as DEMO_IDS
from stage06_demo import guard, local_urls

from app.agent.workspace import AttemptWorkspaceManager
from app.business.models import Attempt, CourseMembership, User
from app.business.python_basics import STARTER

logging.getLogger("sqlalchemy").setLevel(logging.WARNING)

NAMESPACE = uuid.UUID("8b758387-f591-4244-8108-7505be8e8de2")
SAMPLE_EMAIL_PREFIX = "pyb01-acceptance"
SAMPLES = {
    "boundary_error": (
        "演示验收｜负数边界错误",
        "first = int(input())\nsecond = int(input())\nprint(0 if first < 0 or second < 0 else first + second)\n",
    ),
    "correct": (
        "演示验收｜正确程序",
        "first = int(input())\nsecond = int(input())\nprint(first + second)\n",
    ),
}


def stable_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def seed_acceptance_attempts(database_url: str) -> dict[str, tuple[str, str]]:
    guard(database_url)
    engine = create_engine(database_url)
    identities: dict[str, tuple[str, str]] = {}
    try:
        with Session(engine) as session:
            for key, (display_name, _source) in SAMPLES.items():
                user_id = stable_id(f"user-{key}")
                attempt_id = stable_id(f"attempt-{key}")
                existing = session.get(Attempt, attempt_id)
                if existing is None:
                    if session.get(User, user_id) is not None:
                        raise RuntimeError(f"{key} acceptance user already exists without its attempt")
                    session.add(User(
                        id=user_id,
                        email=f"{SAMPLE_EMAIL_PREFIX}-{key}@demo.invalid",
                        display_name=display_name,
                        system_role="student",
                    ))
                    session.flush()
                    session.add(CourseMembership(
                        id=stable_id(f"membership-{key}"),
                        course_id=task_id("course"), user_id=user_id, role="student",
                    ))
                    session.flush()
                    session.add(Attempt(
                        id=attempt_id, task_version_id=task_id("version"),
                        learner_id=user_id, current_stage_id=task_id("stage"),
                        mode="guided_practice", status="active",
                    ))
                elif existing.learner_id != user_id or existing.task_version_id != task_id("version"):
                    raise RuntimeError(f"{key} acceptance attempt has unexpected ownership")
                identities[key] = (user_id, attempt_id)
            session.commit()
        manager = AttemptWorkspaceManager()
        for _user_id, attempt_id in identities.values():
            source = manager.source_directory(attempt_id)
            program = source / "main.py"
            if not program.exists():
                program.write_text(STARTER, encoding="utf-8")
    finally:
        engine.dispose()
    return identities


def request(client: httpx.Client, method: str, path: str, user_id: str,
            *, payload: dict | None = None, status: int = 200) -> dict:
    response = client.request(method, path, headers={"X-User-Id": user_id}, json=payload)
    if response.status_code != status:
        raise AssertionError(f"{method} {path}: HTTP {response.status_code}, {response.text[:800]}")
    return response.json()


def run_sample(client: httpx.Client, key: str, user_id: str, attempt_id: str) -> dict:
    base = f"/api/product/attempts/{attempt_id}"
    workbench = request(client, "GET", base + "/workbench", user_id)
    assert workbench["task"]["key"] == "PYB-01"
    snapshot = workbench["latest_snapshot"]
    if snapshot is None:
        current = request(client, "GET", base + "/files/main.py", user_id)
        source = SAMPLES[key][1]
        saved = request(client, "PUT", base + "/files/main.py", user_id,
                        payload={"content": source, "expected_hash": current["hash"]})
        assert saved["hash"] == hashlib.sha256(source.encode()).hexdigest()
        snapshot = request(client, "POST", base + "/snapshots", user_id,
                           payload={"operation_id": f"live-qa-snapshot-{key}",
                                    "expected_file_hash": saved["hash"],
                                    "expected_file_path": "main.py"})
        snapshot_id = snapshot["id"]
    else:
        snapshot_id = snapshot["id"]
    workbench = request(client, "GET", base + "/workbench", user_id)
    requirements = {item["key"]: item["status"] for item in workbench["requirements"]}
    if requirements.get("addition_public_tests") != "SATISFIED":
        public = request(client, "POST", base + "/runs", user_id,
                         payload={"operation_id": f"live-qa-public-{key}-{uuid.uuid4().hex[:8]}",
                                  "snapshot_id": snapshot_id,
                                  "expected_state_version": workbench["attempt"]["state_version"]})
        assert public["last_tool_status"] == "succeeded", public
        workbench = request(client, "GET", base + "/workbench", user_id)
        requirements = {item["key"]: item["status"] for item in workbench["requirements"]}
    assert requirements["addition_public_tests"] == "SATISFIED", requirements
    submission_op = f"live-qa-submit-{key}"
    if requirements.get("addition_hidden_tests") != (
        "NOT_SATISFIED" if key == "boundary_error" else "SATISFIED"
    ):
        submitted = request(client, "POST", base + "/submissions", user_id,
                            payload={"operation_id": submission_op, "snapshot_id": snapshot_id,
                                     "explanation": "阶段 02 真实容器验收样本。"},
                            status=409 if key == "boundary_error" else 200)
        if key == "boundary_error":
            assert submitted["detail"]["code"] == "hidden_check_failed", submitted
        else:
            assert submitted["snapshot_id"] == snapshot_id, submitted
    workbench = request(client, "GET", base + "/workbench", user_id)
    requirements = {item["key"]: item["status"] for item in workbench["requirements"]}
    expected_hidden = "NOT_SATISFIED" if key == "boundary_error" else "SATISFIED"
    assert requirements["addition_hidden_tests"] == expected_hidden, requirements
    assert bool(workbench["latest_submission"]) == (key == "correct")
    teacher_view = request(client, "GET", f"/api/product/teacher/attempts/{attempt_id}",
                           DEMO_IDS["teacher"])
    teacher_results = {item["key"]: item["status"] for item in teacher_view["requirements"]}
    assert teacher_results == requirements, (teacher_results, requirements)
    hidden = request(client, "GET", base + "/evidence/tool:submit-hidden:" + submission_op, user_id)
    hidden_text = json.dumps(hidden, ensure_ascii=False)
    assert "-4" not in hidden_text and "789012" not in hidden_text and "912468" not in hidden_text
    assert hidden["snapshot"] == snapshot["label"]
    return {
        "user_id": user_id, "attempt_id": attempt_id, "snapshot_id": snapshot_id,
        "public": requirements["addition_public_tests"],
        "hidden": requirements["addition_hidden_tests"],
        "submission_created": bool(workbench["latest_submission"]),
        "teacher_drilldown": True,
        "hidden_cases_exposed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-stage02b-config", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if not args.local_stage02b_config:
        raise RuntimeError("Explicit --local-stage02b-config is required")
    if args.base_url not in {"http://127.0.0.1:8000", "http://localhost:8000"}:
        raise RuntimeError("Live acceptance must target the loopback demo API")
    identities = seed_acceptance_attempts(local_urls()[0])
    with httpx.Client(base_url=args.base_url, timeout=180, trust_env=False) as client:
        results = {key: run_sample(client, key, *identities[key]) for key in SAMPLES}
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
