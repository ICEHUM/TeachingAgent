"""Run the Stage 06 demo readiness gate and emit only bounded operational facts."""

from __future__ import annotations

# A preflight must report every subsystem failure instead of aborting at the first probe.
# ruff: noqa: BLE001
import argparse
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import httpx
import psycopg
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.agent.runner import DEFAULT_PYTHON_RUNNER_IMAGE
from app.agent.teaching_llm import DeepSeekTeachingLLM
from app.agent.workspace import (
    AttemptWorkspaceManager,
    attempt_snapshot_workspace,
    container_security_facts,
    docker_command,
)
from app.business.models import Attempt, Course, User
from app.teaching_control.protocols import GuidanceRequest

EXPECTED_HEAD = "20260927_0007"
DEMO_COURSE = "DEMO-FAQ-001-RC06"
DEMO_ATTEMPT = "24fa0a80-9b5f-55a9-a5b2-8ac6e03c66f7"


def local_urls() -> tuple[str, str]:
    info = json.loads((Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8"))
    return (
        f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}@{info['host']}:{info['port']}/{info['database']}",
        f"postgresql://langgraph_cp:{quote_plus(info['langgraph_cp_password'])}@{info['host']}:{info['port']}/{info['database']}",
    )


def result(name: str, status: str, summary: str) -> dict[str, str]:
    return {"name": name, "status": status, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("TEACHING_DATABASE_URL"))
    parser.add_argument("--checkpoint-url", default=os.getenv("LANGGRAPH_CHECKPOINT_DATABASE_URL"))
    parser.add_argument("--local-stage02b-config", action="store_true")
    parser.add_argument("--student-url", default="http://127.0.0.1:5173")
    parser.add_argument("--teacher-url", default="http://127.0.0.1:5174")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.local_stage02b_config:
        args.database_url, args.checkpoint_url = local_urls()
    checks: list[dict[str, str]] = []
    try:
        engine = create_engine(args.database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("select 1"))
            head = conn.execute(text("select version_num from teaching_business.alembic_version")).scalar_one()
        checks.append(result("PostgreSQL连接", "PASS", "业务数据库可连接"))
        checks.append(result("migration=head", "PASS" if head == EXPECTED_HEAD else "FAIL", f"当前 {head}；目标 {EXPECTED_HEAD}"))
        with Session(engine) as session:
            course = session.scalar(select(Course).where(Course.code == DEMO_COURSE))
            users = list(session.scalars(select(User).where(User.email.in_(["teacher.rc06@demo.invalid", "student.rc06@demo.invalid"]))))
            attempt = session.get(Attempt, DEMO_ATTEMPT)
        checks.append(result("演示账号", "PASS" if len(users) == 2 else "FAIL", f"已识别 {len(users)}/2 个演示账号"))
        checks.append(result("FAQ演示数据", "PASS" if course and attempt else "FAIL", "FAQ-001-v1 演示课程与 Attempt 已就绪" if course and attempt else "演示数据不完整"))
        engine.dispose()
    except Exception as exc:
        checks.append(result("PostgreSQL连接", "FAIL", f"业务数据库不可用：{type(exc).__name__}"))
    try:
        with psycopg.connect(args.checkpoint_url, autocommit=True) as conn:
            count = conn.execute("select count(*) from checkpoints").fetchone()[0]
        checks.append(result("LangGraph checkpoint", "PASS", f"checkpoint 表可读；记录数 {count}"))
    except Exception as exc:
        checks.append(result("LangGraph checkpoint", "FAIL", f"checkpoint 不可用：{type(exc).__name__}"))
    try:
        config = json.loads((ROOT / "workspaces" / "agent-server-image.json").read_text(encoding="utf-8"))
        image = config["server_image"]
        inspected = docker_command("image", "inspect", image, "--format", "{{.Id}}", timeout=20, check=False)
        checks.append(result("OpenHands Agent Server / image", "PASS" if inspected.returncode == 0 else "FAIL", "OpenHands 1.49.2 固定镜像存在" if inspected.returncode == 0 else "固定镜像不可用"))
        runner_image = os.environ.get("TEACHING_PYTHON_RUNNER_IMAGE", DEFAULT_PYTHON_RUNNER_IMAGE).strip()
        if runner_image:
            runner_inspected = docker_command("image", "inspect", runner_image, "--format", "{{.Id}}", timeout=20, check=False)
            checks.append(result(
                "Python Runner / image", "PASS" if runner_inspected.returncode == 0 else "FAIL",
                "固定 Python 镜像存在" if runner_inspected.returncode == 0 else "固定 Python 镜像不可用",
            ))
        else:
            checks.append(result("Python Runner / image", "WARN", "已显式关闭，PYB-01 暂走旧执行器"))
        manager = AttemptWorkspaceManager()
        snapshot_id = "preflight-rc06"
        manager.create_snapshot(attempt_id=DEMO_ATTEMPT, snapshot_id=snapshot_id)
        with attempt_snapshot_workspace(manager=manager, attempt_id=DEMO_ATTEMPT, snapshot_id=snapshot_id) as running:
            facts = container_security_facts(running.container_id)
            secure = facts["network_mode"] == "teachingagent-stage03-internal" and not facts["docker_socket_mounted"] and facts["memory_bytes"] > 0 and facts["pids_limit"] > 0
        checks.append(result("Docker workspace创建", "PASS" if secure else "FAIL", "只读 Snapshot、internal network 与资源限制已验证" if secure else "workspace 安全事实不符合策略"))
    except Exception as exc:
        checks.append(result("Docker workspace创建", "FAIL", f"workspace 创建失败：{type(exc).__name__}"))
    try:
        names = {"inspect_workspace", "run_student_program", "run_faq_tests", "validate_retrieval", "validate_citations", "inspect_runtime_error"}
        source = (ROOT / "backend" / "app" / "agent" / "tools.py").read_text(encoding="utf-8")
        checks.append(result("FAQ工具", "PASS" if all(name in source for name in names) else "FAIL", "6 个 FAQ-001 工具合同存在"))
    except Exception as exc:
        checks.append(result("FAQ工具", "FAIL", f"工具合同读取失败：{type(exc).__name__}"))
    try:
        request = GuidanceRequest(attempt_id=DEMO_ATTEMPT, task_version="FAQ-001-v1", stage="implement_retrieval", mode="guided_practice", level="L0", kind="question", evidence_refs=("preflight://evidence",), evidence_summary="已知问题检索为空", operation_id=f"stage06-preflight-{time.time_ns()}")
        draft = DeepSeekTeachingLLM(timeout_seconds=8).generate_guidance(request)
        checks.append(result("DeepSeek模型连通", "PASS" if draft.success else "WARN", "模型调用成功" if draft.success else f"已验证安全降级：{draft.fallback_reason}"))
    except Exception as exc:
        checks.append(result("DeepSeek模型连通", "WARN", f"模型检查进入降级：{type(exc).__name__}"))
    for label, url in (("学生前端", args.student_url), ("教师前端", args.teacher_url)):
        try:
            response = httpx.get(url, timeout=5, trust_env=False)
            checks.append(result(label, "PASS" if response.status_code < 500 else "FAIL", f"HTTP {response.status_code}"))
        except Exception as exc:
            checks.append(result(label, "FAIL", f"不可访问：{type(exc).__name__}"))
    free_gb = shutil.disk_usage(ROOT).free / (1024 ** 3)
    checks.append(result("磁盘空间", "PASS" if free_gb >= 5 else "WARN", f"可用 {free_gb:.1f} GiB"))
    port_summaries = []
    ports_ok = True
    for port in (8000, 5173, 5174):
        with socket.socket() as sock:
            sock.settimeout(1); ok = sock.connect_ex(("127.0.0.1", port)) == 0
        ports_ok &= ok; port_summaries.append(f"{port}:{'open' if ok else 'closed'}")
    checks.append(result("必要端口", "PASS" if ports_ok else "FAIL", "，".join(port_summaries)))
    overall = "FAIL" if any(item["status"] == "FAIL" for item in checks) else ("WARN" if any(item["status"] == "WARN" for item in checks) else "PASS")
    report = {"overall": overall, "demo_ready": overall != "FAIL", "checks": checks, "policy": "存在 FAIL 时禁止进入演示状态"}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = args.output if args.output.is_absolute() else ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text(payload, encoding="utf-8")
    print(payload)
    if overall == "FAIL":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
