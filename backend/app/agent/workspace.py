"""Local Windows Docker Desktop workspaces with per-session directories and credentials."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

import httpx
from openhands.sdk.workspace import RemoteWorkspace

ROOT = Path(__file__).resolve().parents[3]


def docker_command(*args, timeout=60, env=None, check=True):
    executable = Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
    if not executable.exists():
        executable = Path(os.environ["ProgramFiles"]) / "Docker/Docker/resources/bin/docker.exe"
    result = subprocess.run(
        [str(executable), "--host", "npipe:////./pipe/dockerDesktopLinuxEngine", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "Docker command failed")
    return result


@dataclass
class RunningWorkspace:
    container_id: str
    directory: Path
    url: str
    workspace: RemoteWorkspace = field(repr=False)


@contextmanager
def isolated_workspace():
    """Create one Linux container; remove it on exit while retaining its workspace files."""
    config = json.loads((ROOT / "workspaces/agent-server-image.json").read_text(encoding="utf-8"))
    image = config["server_image"]
    # Require an already-pulled, pinned image; no silent latest-version fallback.
    docker_command("image", "inspect", image, "--format", "{{.Id}}")
    name = "teachingagent-" + uuid.uuid4().hex
    directory = ROOT / "workspaces/runtime" / name
    directory.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(32)
    process_env = dict(os.environ, OH_SESSION_API_KEYS_0=token)
    container_id = None
    workspace = None
    try:
        result = docker_command(
            "run", "--detach", "--rm", "--pull=never", "--init", "--name", name,
            "--label", "teachingagent.managed=true", "--platform", config["platform"],
            "--memory", "2g", "--cpus", "2", "--pids-limit", "512",
            "--publish", "127.0.0.1::8000", "--env", "OH_SESSION_API_KEYS_0",
            "--mount", f"type=bind,source={directory},target=/workspace",
            image, "--host", "0.0.0.0", "--port", "8000", env=process_env,
        )
        container_id = result.stdout.strip()
        ports = json.loads(docker_command("inspect", "--format", "{{json .NetworkSettings.Ports}}", container_id).stdout)
        binding = ports["8000/tcp"][0]
        if binding["HostIp"] != "127.0.0.1":
            raise RuntimeError("Workspace API must bind to localhost.")
        url = f"http://127.0.0.1:{binding['HostPort']}"
        deadline = time.monotonic() + 120
        with httpx.Client(headers={"X-Session-API-Key": token}, timeout=3, trust_env=False) as client:
            while True:
                try:
                    if client.get(url + "/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                running = docker_command("inspect", "--format", "{{.State.Running}}", container_id, check=False)
                if running.stdout.strip() != "true" or time.monotonic() >= deadline:
                    logs = docker_command("logs", "--tail", "30", container_id, check=False)
                    raise RuntimeError("Agent Server failed to become ready: " + (logs.stdout + logs.stderr).replace(token, "[REDACTED]"))
                time.sleep(2)
        workspace = RemoteWorkspace(host=url, api_key=token, working_dir="/workspace", read_timeout=90)
        yield RunningWorkspace(container_id, directory, url, workspace)
    finally:
        if workspace is not None:
            workspace.reset_client()
        if container_id:
            result = docker_command("rm", "--force", container_id, check=False)
            if result.returncode and "No such container" not in result.stderr:
                raise RuntimeError("Workspace cleanup failed: " + result.stderr)
