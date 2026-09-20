"""Prepare and advance the real PostgreSQL fixture used by the Stage 05B browser E2E."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import runpy
import sys
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_PATH = ROOT / ".runtime" / "stage05b-browser-context.json"
HELPERS = runpy.run_path(str(ROOT / "scripts" / "stage05b-real-verify.py"), run_name="stage05b_fixture_helpers")
API = "http://127.0.0.1:8000"


async def prepare() -> dict:
    info = HELPERS["db_info"]()
    database_url = HELPERS["app_url"](info)
    os.environ["TEACHING_DATABASE_URL"] = database_url
    prepared = await HELPERS["prepare_attempt"](database_url)
    attempt_id = prepared["attempt_id"]
    headers = {"X-User-Id": prepared["student_id"]}
    unique = uuid4().hex[:10]
    manager = HELPERS["AttemptWorkspaceManager"]()
    source = manager.source_directory(attempt_id)
    original = (source / "faq_app.py").read_text(encoding="utf-8")
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as client:
        saved_a = await client.put(
            f"/api/product/attempts/{attempt_id}/files/faq_app.py",
            headers=headers,
            json={"content": HELPERS["BROKEN"], "expected_hash": hashlib.sha256(original.encode()).hexdigest()},
        )
        saved_a.raise_for_status()
        snapshot_a = await client.post(
            f"/api/product/attempts/{attempt_id}/snapshots",
            headers=headers,
            json={"operation_id": f"browser-a-{unique}", "expected_file_hash": saved_a.json()["hash"]},
        )
        snapshot_a.raise_for_status()
        passing = (HELPERS["ORIGIN_B_DIR"] / "faq_app.py").read_text(encoding="utf-8")
        saved_b = await client.put(
            f"/api/product/attempts/{attempt_id}/files/faq_app.py",
            headers=headers,
            json={"content": passing, "expected_hash": saved_a.json()["hash"]},
        )
        saved_b.raise_for_status()
        snapshot_b = await client.post(
            f"/api/product/attempts/{attempt_id}/snapshots",
            headers=headers,
            json={"operation_id": f"browser-b-{unique}", "expected_file_hash": saved_b.json()["hash"]},
        )
        snapshot_b.raise_for_status()
    await HELPERS["bind_b_results"](database_url, attempt_id=attempt_id, snapshot_id=snapshot_b.json()["id"])
    context = {
        **prepared,
        "snapshot_a": snapshot_a.json()["id"],
        "snapshot_b": snapshot_b.json()["id"],
        "saved_b_hash": saved_b.json()["hash"],
        "unique": unique,
    }
    CONTEXT_PATH.write_text(json.dumps(context, indent=2), encoding="utf-8")
    return context


async def snapshot_c() -> dict:
    context = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    info = HELPERS["db_info"]()
    database_url = HELPERS["app_url"](info)
    headers = {"X-User-Id": context["student_id"]}
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as client:
        saved = await client.put(
            f"/api/product/attempts/{context['attempt_id']}/files/faq_app.py",
            headers=headers,
            json={"content": HELPERS["CHANGED_C"], "expected_hash": context["saved_b_hash"]},
        )
        saved.raise_for_status()
        snapshot = await client.post(
            f"/api/product/attempts/{context['attempt_id']}/snapshots",
            headers=headers,
            json={"operation_id": f"browser-c-{context['unique']}", "expected_file_hash": saved.json()["hash"]},
        )
        snapshot.raise_for_status()
    context["snapshot_c"] = snapshot.json()["id"]
    await HELPERS["bind_c_results"](
        database_url, attempt_id=context["attempt_id"], snapshot_id=context["snapshot_c"]
    )
    CONTEXT_PATH.write_text(json.dumps(context, indent=2), encoding="utf-8")
    return context


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "prepare"
    coroutine = prepare() if action == "prepare" else snapshot_c() if action == "snapshot-c" else None
    if coroutine is None:
        raise SystemExit(f"unknown action: {action}")
    if sys.platform == "win32":
        result = asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
    else:
        result = asyncio.run(coroutine)
    print(json.dumps({
        "action": action,
        "attempt_id": result["attempt_id"],
        "snapshot_b": result["snapshot_b"],
        "snapshot_c": result.get("snapshot_c"),
    }, indent=2))


if __name__ == "__main__":
    main()
