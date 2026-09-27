"""Start the local Stage 05B API and Vite apps with the real PostgreSQL roles."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def spawn(name: str, command: list[str], *, env: dict[str, str]) -> int:
    log = (RUNTIME / f"{name}.log").open("ab")
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
    )
    return process.pid


def main() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    info = json.loads((Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8"))
    login_path = Path(os.environ["TEMP"]) / "teachingagent-stage06-demo-login.json"
    if not login_path.exists():
        raise RuntimeError("Demo login credentials missing; run stage06_demo.py init first")
    login = json.loads(login_path.read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "backend"),
        "TEACHING_ENV": "DEMO",
        "DEV_AUTH_ENABLED": "true",
        "LANGGRAPH_STRICT_MSGPACK": "true",
        "DEMO_STUDENT_ACCOUNT": login["student_account"],
        "DEMO_STUDENT_PASSWORD": login["student_password"],
        "DEMO_TEACHER_ACCOUNT": login["teacher_account"],
        "DEMO_TEACHER_PASSWORD": login["teacher_password"],
        "DEMO_TEACHER2_ACCOUNT": login.get("teacher2_account", "demo_teacher2"),
        "DEMO_TEACHER2_PASSWORD": login.get("teacher2_password", "123456"),
        "DEMO_TEACHER3_ACCOUNT": login.get("teacher3_account", "demo_teacher3"),
        "DEMO_TEACHER3_PASSWORD": login.get("teacher3_password", "123456"),
        "TEACHING_DATABASE_URL": (
            f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}"
            f"@{info['host']}:{info['port']}/{info['database']}"
        ),
        "LANGGRAPH_CHECKPOINT_DATABASE_URL": (
            f"postgresql://langgraph_cp:{quote_plus(info['langgraph_cp_password'])}"
            f"@{info['host']}:{info['port']}/{info['database']}"
        ),
    })
    python = str(ROOT / "backend" / ".venv" / "Scripts" / "python.exe")
    npm = shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("npm.cmd not found")
    processes: dict[str, int | str] = {}
    if port_open(8000):
        processes["api"] = "already-running"
    else:
        processes["api"] = spawn("stage05b-api", [python, str(ROOT / "scripts" / "stage05a-server.py")], env=env)
    if port_open(5173):
        processes["student"] = "already-running"
    else:
        processes["student"] = spawn(
            "stage05b-student", [npm, "run", "dev", "--workspace", "frontend/student", "--", "--port", "5173"], env=env
        )
    if port_open(5174):
        processes["teacher"] = "already-running"
    else:
        processes["teacher"] = spawn(
            "stage05b-teacher", [npm, "run", "dev", "--workspace", "frontend/teacher", "--", "--port", "5174"], env=env
        )
    deadline = time.time() + 30
    while time.time() < deadline:
        if all(port_open(port) for port in (8000, 5173, 5174)):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
        time.sleep(0.25)
    else:
        raise RuntimeError("Stage 05B stack did not become healthy within 30 seconds")
    (RUNTIME / "stage05b-stack-pids.json").write_text(json.dumps(processes, indent=2), encoding="utf-8")
    print(json.dumps({"healthy": True, "ports": [8000, 5173, 5174], "processes": processes}, indent=2))


if __name__ == "__main__":
    main()
