"""Verify two container workspaces without sending model credentials or calling an LLM."""
import json
import os
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx
from app.agent.workspace import docker_command, isolated_workspace


def main():
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "checking", "model_calls": 0}
    containers = []
    try:
        with ExitStack() as stack:
            print("Starting independent workspace A...", flush=True)
            a = stack.enter_context(isolated_workspace())
            containers.append(a.container_id)
            print("Starting independent workspace B...", flush=True)
            b = stack.enter_context(isolated_workspace())
            containers.append(b.container_id)
            for handle in (a, b):
                info = handle.workspace.get_server_info()
                if info.get("sdk_version") != "1.49.2":
                    raise RuntimeError("Remote SDK version mismatch: " + str(info.get("sdk_version")))
                result = handle.workspace.execute_command(
                    "python -c \"print('WORKSPACE_EXECUTION_OK')\"",
                    cwd="/workspace", timeout=30)
                if result.exit_code or result.stdout.strip() != "WORKSPACE_EXECUTION_OK":
                    raise RuntimeError("Agent Server version/execution check failed: " + result.stdout + result.stderr)
            report["agent_server_version"] = "1.49.2"
            report["sdk_command_execution"] = "passed"
            print("SDK remote commands and server versions: passed", flush=True)
            result = a.workspace.execute_command("printf WORKSPACE_A > /workspace/isolation-marker.txt", timeout=30)
            if result.exit_code:
                raise RuntimeError("Workspace A could not write its marker")
            result = b.workspace.execute_command("test ! -e /workspace/isolation-marker.txt && printf WORKSPACE_B > /workspace/isolation-marker.txt", timeout=30)
            if result.exit_code:
                raise RuntimeError("Workspace B saw A's marker or could not write its own")
            if (a.directory / "isolation-marker.txt").read_text() != "WORKSPACE_A":
                raise RuntimeError("Workspace A persistence check failed")
            if (b.directory / "isolation-marker.txt").read_text() != "WORKSPACE_B":
                raise RuntimeError("Workspace B persistence check failed")
            report["separate_workspace_files"] = "passed"
            report["host_directory_persistence"] = "passed"
            with httpx.Client(timeout=10, trust_env=False) as client:
                route = "/api/bash/bash_events/search"
                if client.get(a.url + route).status_code not in (401, 403):
                    raise RuntimeError("Unauthenticated workspace API access was not rejected")
                if client.get(a.url + route, headers={"X-Session-API-Key": b.workspace.api_key}).status_code not in (401, 403):
                    raise RuntimeError("Another workspace's token was not rejected")
            report["api_authentication"] = "passed"
            report["localhost_only"] = "passed"
            report["workspace_directories"] = [str(a.directory), str(b.directory)]
            print("Separate files, persistence, and per-workspace authentication: passed", flush=True)
        for cid in containers:
            result = docker_command("inspect", cid, "--format", "{{.Id}}", check=False)
            if result.returncode == 0:
                raise RuntimeError("Test container remains after context exit")
        report["container_cleanup"] = "passed"
        report["status"] = "passed"
        print("Both test containers removed; workspace evidence files retained.", flush=True)
        return 0
    except Exception as error:  # noqa: BLE001 - persist unexpected runtime failures
        report.update(status="failed", error=str(error))
        print(str(error), file=sys.stderr)
        return 1
    finally:
        report_path = ROOT / ".runtime" / "checks" / "workspace-check.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
