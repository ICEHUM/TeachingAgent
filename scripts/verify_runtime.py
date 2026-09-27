"""Check this project's local Docker/WSL runtime without any model API calls."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".runtime" / "checks" / "runtime-check.json"


def run(args, timeout=30):
    result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    raw = result.stdout + result.stderr
    encoding = "utf-16le" if b"\x00" in raw else "utf-8"
    return result.returncode, raw.decode(encoding, errors="replace").strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    report = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "status": "checking", "container_smoke": "not_run"}
    try:
        install = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "DockerDesktop"
        if not (install / "Docker Desktop.exe").exists():
            install = Path(os.environ["ProgramFiles"]) / "Docker" / "Docker"
        docker = install / "resources" / "bin" / "docker.exe"
        if not docker.exists():
            raise RuntimeError("Docker Desktop is not installed.")
        code, version = run([str(docker), "--version"])
        if code:
            raise RuntimeError(version)
        report["docker_cli"] = version
        print(version, flush=True)
        code, version = run(["wsl.exe", "--version"])
        report["wsl_version"] = version
        if code:
            raise RuntimeError("WSL version check failed: " + version)
        code, virtualization = run(["powershell.exe", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).HypervisorPresent"])
        if code:
            raise RuntimeError("Cannot check Windows virtualization: " + virtualization)
        if virtualization.strip().lower() != "true":
            report["status"] = "restart_required"
            print("Windows restart is required to activate the installed Virtual Machine Platform.")
            return 2
        # Always target this computer's Docker Desktop Linux engine, never a remote context.
        local = [str(docker), "--host", "npipe:////./pipe/dockerDesktopLinuxEngine"]
        def engine_info():
            return run(local + ["info", "--format", "{{.OSType}} {{.ServerVersion}}"], timeout=15)
        if args.start:
            exe = str(install / "Docker Desktop.exe").replace("'", "''")
            code, output = run(["powershell.exe", "-NoProfile", "-Command",
                "if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) "
                "{ Start-Process -FilePath '" + exe + "' -WindowStyle Hidden }"])
            if code:
                raise RuntimeError("Cannot start Docker Desktop: " + output)
        deadline = time.monotonic() + (120 if args.start else 0)
        while True:
            try:
                code, info = engine_info()
            except subprocess.TimeoutExpired:
                code, info = 1, "Docker engine response timed out."
            if code == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Docker Linux engine is not ready. Start Docker Desktop, then retry. " + info)
            print("Waiting for the Docker Linux engine...", flush=True)
            time.sleep(5)
        if not info.startswith("linux "):
            raise RuntimeError("Expected a Linux Docker engine: " + info)
        report["docker_engine"] = info
        print("Docker engine: " + info, flush=True)
        if args.smoke:
            print("Running the official hello-world container (no model API required)...", flush=True)
            code, output = run(local + ["run", "--rm", "hello-world:latest"], timeout=180)
            if code or "Hello from Docker!" not in output:
                raise RuntimeError("Container smoke check failed: " + output)
            report["container_smoke"] = "passed"
            print("Container smoke check passed.", flush=True)
        report["status"] = "ready"
        return 0
    except Exception as exc:  # noqa: BLE001 - persist unexpected runtime failures
        report["status"] = "failed"
        report["error"] = str(exc)
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Runtime report: " + str(REPORT), flush=True)


if __name__ == "__main__":
    sys.exit(main())
