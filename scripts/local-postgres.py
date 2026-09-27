"""Run the project PostgreSQL instance natively on Windows, without Docker Desktop.

The Stage 02B runtime normally hosts PostgreSQL inside the `teachingagent-postgres-02b`
container.  This helper reproduces the same host, port, database, roles and passwords
using a local PostgreSQL server, so every other project script keeps working unchanged
(`stage05b-migrate.py`, `stage06_demo.py --local-stage02b-config`,
`stage05b-start-stack.py`, `stage06_preflight.py`).

Binary discovery order:
  1. `TEACHING_PG_BIN`
  2. the `pgserver` package installed in an isolated environment (see `--help`)
  3. `initdb` on `PATH`

Usage:
  python scripts/local-postgres.py status
  python scripts/local-postgres.py start      # initdb on first run, then start
  python scripts/local-postgres.py setup      # roles, schemas, Alembic, checkpoint tables
  python scripts/local-postgres.py stop
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PGDATA = ROOT / ".runtime" / "pgdata"
PGLOG = ROOT / ".runtime" / "local-postgres.log"


def load_db_config() -> dict:
    """Read the Stage 02B runtime facts that the rest of the project already uses."""
    path = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "teachingagent-02b-db.json"
    if not path.exists():
        raise RuntimeError(f"Missing {path}; run the Stage 02B runtime setup first")
    return json.loads(path.read_text(encoding="utf-8"))


def find_pg_bin() -> Path:
    explicit = os.environ.get("TEACHING_PG_BIN")
    if explicit:
        candidate = Path(explicit)
        if (candidate / "pg_ctl.exe").exists():
            return candidate
        raise RuntimeError(f"TEACHING_PG_BIN does not contain pg_ctl.exe: {candidate}")
    try:
        import pgserver  # type: ignore

        package_bin = Path(pgserver.__file__).parent / "pginstall" / "bin"
        if (package_bin / "pg_ctl.exe").exists():
            return package_bin
    except ImportError:
        pass
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent
    raise RuntimeError(
        "No PostgreSQL binaries found. Install the isolated host environment first:\n"
        '  "D:\\Anaconda\\python.exe" -m venv "$env:USERPROFILE\\.workbuddy\\binaries\\python\\envs\\pgserver312"\n'
        "  ...\\Scripts\\python.exe -m pip install --index-url https://pypi.org/simple pgserver\n"
        "or point TEACHING_PG_BIN at an existing PostgreSQL bin directory."
    )


def pg_env(info: dict, *, password: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = password if password is not None else info["admin_password"]
    env["PGHOST"] = info["host"]
    env["PGPORT"] = str(info["port"])
    env["PGUSER"] = info["admin_user"]
    env["PGCLIENTENCODING"] = "UTF8"
    return env


def run(command: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None,
        input_text: str | None = None, check: bool = True,
        capture: bool = True) -> subprocess.CompletedProcess:
    """Run a helper binary.

    `capture=False` is required for `pg_ctl start`: the background server inherits the
    parent's stdout/stderr handles, so a captured pipe never reaches EOF and the call
    would block forever even though PostgreSQL is already accepting connections.
    """
    print("+", " ".join(str(part) for part in command[:6]), "..." if len(command) > 6 else "")
    return subprocess.run(
        [str(part) for part in command],
        env=env,
        cwd=str(cwd) if cwd else None,
        input=input_text,
        text=True,
        capture_output=capture,
        stdout=None if capture else subprocess.DEVNULL,
        stderr=None if capture else subprocess.DEVNULL,
        check=check,
    )


def port_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def initialised() -> bool:
    return (PGDATA / "PG_VERSION").exists()


def init_data_directory(info: dict, bin_dir: Path) -> None:
    PGDATA.parent.mkdir(parents=True, exist_ok=True)
    password_file = PGDATA.parent / ".pg-init-password"
    password_file.write_text(info["admin_password"], encoding="utf-8")
    try:
        result = run(
            [
                bin_dir / "initdb.exe",
                "-D", PGDATA,
                "-U", info["admin_user"],
                "--auth-local=trust",
                "--auth-host=scram-sha-256",
                "--pwfile", password_file,
                "--encoding=UTF8",
                "--locale=C",
            ]
        )
    finally:
        password_file.unlink(missing_ok=True)
    print(result.stdout.strip()[-400:])


def start(info: dict, bin_dir: Path) -> None:
    if not initialised():
        print(f"initialising new data directory at {PGDATA}")
        init_data_directory(info, bin_dir)
    if port_open(info["host"], info["port"]):
        print(f"a server is already listening on {info['host']}:{info['port']}")
        return
    PGLOG.parent.mkdir(parents=True, exist_ok=True)
    result = run(
        [
            bin_dir / "pg_ctl.exe",
            "-D", PGDATA,
            "-l", PGLOG,
            "-o", f'-h {info["host"]} -p {info["port"]}',
            "-w",
            "-t", "60",
            "start",
        ],
        check=False,
        capture=False,
    )
    print((result.stdout or result.stderr or "").strip() or "(pg_ctl start finished)")
    if not port_open(info["host"], info["port"]):
        log_tail = PGLOG.read_text(encoding="utf-8", errors="replace")[-1500:] if PGLOG.exists() else "(no log)"
        raise RuntimeError(f"PostgreSQL did not start on {info['host']}:{info['port']}\n{log_tail}")
    print(f"PostgreSQL {info['database']} listening on {info['host']}:{info['port']}")


def stop(bin_dir: Path, info: dict) -> None:
    if not initialised():
        print("no local data directory; nothing to stop")
        return
    result = run([bin_dir / "pg_ctl.exe", "-D", PGDATA, "-m", "fast", "-w", "stop"], check=False)
    print((result.stdout.strip() or result.stderr.strip()) or "stopped")


def status(info: dict, bin_dir: Path) -> None:
    print(f"pgdata          : {PGDATA} ({'initialised' if initialised() else 'not initialised'})")
    print(f"binaries        : {bin_dir}")
    print(f"target          : {info['host']}:{info['port']}/{info['database']} (admin {info['admin_user']})")
    print(f"port listening  : {port_open(info['host'], info['port'])}")
    if initialised():
        result = run([bin_dir / "pg_ctl.exe", "-D", PGDATA, "status"], check=False)
        print(f"pg_ctl status   : {(result.stdout or result.stderr).strip()}")


def database_exists(info: dict, bin_dir: Path) -> bool:
    result = run(
        [bin_dir / "psql.exe", "-d", "postgres", "-tAc",
         f"SELECT 1 FROM pg_database WHERE datname = '{info['database']}'"],
        env=pg_env(info), check=False,
    )
    return result.stdout.strip() == "1"


def setup(info: dict, bin_dir: Path) -> None:
    """Create roles/schemas, migrate the business schema and initialise checkpoints."""
    if not database_exists(info, bin_dir):
        run([bin_dir / "createdb.exe", "-O", info["admin_user"], info["database"]], env=pg_env(info))

    bootstrap = ROOT / "deploy" / "bootstrap-postgres.sql"
    run([bin_dir / "psql.exe", "-v", "ON_ERROR_STOP=1", "-d", info["database"], "-f", bootstrap],
        env=pg_env(info))

    role_passwords = (
        f"ALTER ROLE teaching_app PASSWORD '{info['teaching_app_password']}';\n"
        f"ALTER ROLE langgraph_cp PASSWORD '{info['langgraph_cp_password']}';\n"
    )
    run([bin_dir / "psql.exe", "-v", "ON_ERROR_STOP=1", "-d", info["database"]],
        env=pg_env(info), input_text=role_passwords)
    print("roles and schemas ready")

    python = BACKEND / ".venv" / "Scripts" / "python.exe"
    admin_url = (
        f"postgresql+psycopg://{info['admin_user']}:{info['admin_password']}"
        f"@{info['host']}:{info['port']}/{info['database']}"
    )
    checkpoint_url = (
        f"postgresql://langgraph_cp:{info['langgraph_cp_password']}"
        f"@{info['host']}:{info['port']}/{info['database']}"
    )
    migrate_env = os.environ.copy()
    migrate_env.update({
        "PYTHONPATH": str(BACKEND),
        "TEACHING_ALEMBIC_DATABASE_URL": admin_url,
    })
    migrated = run([python, "-m", "alembic", "upgrade", "head"], env=migrate_env, cwd=BACKEND, check=False)
    print((migrated.stdout or migrated.stderr).strip()[-600:])
    if migrated.returncode:
        raise RuntimeError("Alembic upgrade failed")

    checkpoint_env = os.environ.copy()
    checkpoint_env.update({
        "PYTHONPATH": str(BACKEND),
        "LANGGRAPH_CHECKPOINT_DATABASE_URL": checkpoint_url,
        "LANGGRAPH_STRICT_MSGPACK": "true",
    })
    checkpoint = run([python, BACKEND / "scripts" / "init_checkpoint.py"], env=checkpoint_env,
                     cwd=ROOT, check=False)
    print((checkpoint.stdout or checkpoint.stderr).strip()[-600:])
    if checkpoint.returncode:
        raise RuntimeError("LangGraph checkpoint initialisation failed")

    summary = run(
        [bin_dir / "psql.exe", "-d", info["database"], "-tAc",
         """
         SELECT 'tables=' || (SELECT count(*) FROM information_schema.tables
                              WHERE table_schema = 'teaching_business')
             || ' checkpoint=' || (SELECT count(*) FROM information_schema.tables
                                   WHERE table_schema = 'langgraph_checkpoint')
             || ' alembic=' || (SELECT version_num FROM teaching_business.alembic_version)
         """],
        env=pg_env(info), check=False,
    )
    print("ready:", summary.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["status", "start", "setup", "stop", "restart"])
    args = parser.parse_args()
    info = load_db_config()
    bin_dir = find_pg_bin()
    if args.action == "status":
        status(info, bin_dir)
    elif args.action == "start":
        start(info, bin_dir)
    elif args.action == "stop":
        stop(bin_dir, info)
    elif args.action == "restart":
        stop(bin_dir, info)
        start(info, bin_dir)
    else:
        start(info, bin_dir)
        setup(info, bin_dir)
        status(info, bin_dir)


if __name__ == "__main__":
    sys.exit(main())
