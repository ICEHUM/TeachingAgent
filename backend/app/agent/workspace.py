"""Docker Desktop workspaces with per-attempt snapshot isolation."""

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import httpx
from openhands.sdk.workspace import RemoteWorkspace

ROOT = Path(__file__).resolve().parents[3]
ATTEMPT_ROOT = ROOT / "workspaces" / "attempts"
EVIDENCE_ROOT = ROOT / "workspaces" / "evidence"
INTERNAL_NETWORK = "teachingagent-stage03-internal"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def docker_executable() -> Path:
    if os.name != "nt":
        found = shutil.which("docker")
        if not found:
            raise RuntimeError("Docker CLI is not installed on the Linux host")
        return Path(found)
    executable = Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
    if not executable.exists():
        executable = Path(os.environ["ProgramFiles"]) / "Docker/Docker/resources/bin/docker.exe"
    return executable


def _docker_cli_argv(executable: Path, platform: str, linux_socket: str) -> list[str]:
    if platform == "nt":
        return [str(executable), "--host", "npipe:////./pipe/dockerDesktopLinuxEngine"]
    socket = PurePosixPath(linux_socket)
    if not socket.is_absolute() or ".." in socket.parts or any(char in linux_socket for char in "\r\n\0"):
        raise ValueError("Docker host must be an absolute local Unix socket")
    return [str(executable), "--host", "unix://" + str(socket)]


def docker_cli_argv() -> list[str]:
    return _docker_cli_argv(
        docker_executable(), os.name,
        os.environ.get("TEACHING_DOCKER_UNIX_SOCKET", "/var/run/docker.sock"),
    )


def docker_command(*args, timeout=60, env=None, check=True):
    result = subprocess.run(
        [*docker_cli_argv(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
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


@dataclass(frozen=True, slots=True)
class WorkspaceSecurityPolicy:
    cpu_count: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 128
    tmpfs_mb: int = 128
    startup_timeout_seconds: int = 120
    network_name: str = INTERNAL_NETWORK


def _digest_id(value: str) -> str:
    if not value or len(value) > 160:
        raise ValueError("workspace identifier is empty or too long")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _validate_snapshot_id(snapshot_id: str) -> str:
    if not SAFE_ID.fullmatch(snapshot_id):
        raise ValueError("snapshot_id contains unsafe path characters")
    return snapshot_id


def _tree_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError("workspace snapshots cannot contain symbolic links")
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _prepare_snapshot_for_container(destination: Path) -> None:
    """Expose only an immutable snapshot to the unprivileged container user."""
    if destination.is_symlink():
        raise ValueError("workspace snapshots cannot contain symbolic links")
    for path in destination.rglob("*"):
        if path.is_symlink():
            raise ValueError("workspace snapshots cannot contain symbolic links")
        if path.is_dir():
            if os.name != "nt":
                path.chmod(0o555)
        elif path.is_file():
            if os.name != "nt":
                path.chmod(0o444)
        else:
            raise ValueError("workspace snapshots must contain only files and directories")
    if os.name != "nt":
        destination.chmod(0o555)


class AttemptWorkspaceManager:
    """Keeps mutable student sources separate from immutable execution snapshots."""

    def __init__(self, root: Path = ATTEMPT_ROOT, evidence_root: Path = EVIDENCE_ROOT):
        self.root = root.resolve()
        self.evidence_root = evidence_root.resolve()

    def attempt_directory(self, attempt_id: str) -> Path:
        return self.root / ("attempt-" + _digest_id(attempt_id))

    def source_directory(self, attempt_id: str) -> Path:
        directory = self.attempt_directory(attempt_id) / "source"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def snapshot_directory(self, attempt_id: str, snapshot_id: str) -> Path:
        safe_snapshot = _validate_snapshot_id(snapshot_id)
        directory = self.attempt_directory(attempt_id) / "snapshots" / safe_snapshot
        resolved_parent = directory.parent.resolve()
        expected_parent = (self.attempt_directory(attempt_id) / "snapshots").resolve()
        if resolved_parent != expected_parent:
            raise ValueError("snapshot path escaped its attempt directory")
        return directory

    def create_snapshot(self, *, attempt_id: str, snapshot_id: str) -> tuple[Path, str]:
        source = self.source_directory(attempt_id)
        for path in source.rglob("*"):
            if path.is_symlink():
                raise ValueError("workspace source cannot contain symbolic links")
        destination = self.snapshot_directory(attempt_id, snapshot_id)
        if destination.exists():
            _prepare_snapshot_for_container(destination)
            digest = _tree_digest(destination)
            return destination, f"workspace://{_digest_id(attempt_id)}/{snapshot_id}#{digest}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        _prepare_snapshot_for_container(destination)
        digest = _tree_digest(destination)
        metadata = {
            "attempt_digest": _digest_id(attempt_id),
            "snapshot_id": snapshot_id,
            "sha256": digest,
        }
        metadata_path = destination.parent / f"{snapshot_id}.json"
        metadata_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        return destination, f"workspace://{_digest_id(attempt_id)}/{snapshot_id}#{digest}"

    def evidence_directory(self, attempt_id: str) -> Path:
        directory = self.evidence_root / ("attempt-" + _digest_id(attempt_id))
        directory.mkdir(parents=True, exist_ok=True)
        return directory


def ensure_internal_network(name: str = INTERNAL_NETWORK) -> None:
    inspected = docker_command("network", "inspect", name, check=False)
    if inspected.returncode:
        docker_command(
            "network",
            "create",
            "--driver",
            "bridge",
            "--internal",
            "--label",
            "teachingagent.managed=true",
            name,
        )
    internal = docker_command(
        "network", "inspect", name, "--format", "{{.Internal}}"
    ).stdout.strip()
    if internal.lower() != "true":
        raise RuntimeError("Workspace network must be an internal Docker network.")


def container_security_facts(container_id: str) -> dict[str, object]:
    """Return bounded, non-secret Docker facts for the evidence manifest."""

    inspected = json.loads(docker_command("inspect", container_id).stdout)[0]
    host = inspected["HostConfig"]
    config = inspected["Config"]
    mounts = [
        {
            "destination": item["Destination"],
            "read_write": bool(item["RW"]),
            "type": item["Type"],
        }
        for item in inspected.get("Mounts", [])
    ]
    return {
        "image_id": inspected.get("Image"),
        "network_mode": host.get("NetworkMode"),
        "memory_bytes": host.get("Memory"),
        "memory_swap_bytes": host.get("MemorySwap"),
        "read_only_root": bool(host.get("ReadonlyRootfs")),
        "nano_cpus": host.get("NanoCpus"),
        "pids_limit": host.get("PidsLimit"),
        "cap_drop": host.get("CapDrop") or [],
        "cap_add": host.get("CapAdd") or [],
        "security_opt": host.get("SecurityOpt") or [],
        "container_user": config.get("User"),
        "mounts": mounts,
        "published_ports": sorted((config.get("ExposedPorts") or {}).keys()),
        "port_bindings": host.get("PortBindings") or {},
        "docker_socket_mounted": any(
            item["destination"] == "/var/run/docker.sock" for item in mounts
        ),
        "container_env_names": sorted(
            value.split("=", 1)[0] for value in config.get("Env", [])
        ),
    }


def _fixed_tcp_proxy_command(target: str) -> str:
    source = f"""
import asyncio

async def relay(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()

async def handle(client_reader, client_writer):
    server_reader, server_writer = await asyncio.open_connection({target!r}, 8000)
    await asyncio.gather(
        relay(client_reader, server_writer),
        relay(server_reader, client_writer),
    )

async def main():
    server = await asyncio.start_server(handle, '0.0.0.0', 9000)
    async with server:
        await server.serve_forever()

asyncio.run(main())
"""
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return f"import base64;exec(base64.b64decode('{encoded}'))"


@contextmanager
def attempt_snapshot_workspace(
    *,
    manager: AttemptWorkspaceManager,
    attempt_id: str,
    snapshot_id: str,
    policy: WorkspaceSecurityPolicy | None = None,
):
    """Run OpenHands with one read-only snapshot mounted at /workspace/student."""

    limits = policy or WorkspaceSecurityPolicy()
    directory = manager.snapshot_directory(attempt_id, snapshot_id)
    if not directory.is_dir():
        raise RuntimeError("Workspace snapshot does not exist.")
    config = json.loads(
        (ROOT / "workspaces/agent-server-image.json").read_text(encoding="utf-8")
    )
    image = config["server_image"]
    docker_command("image", "inspect", image, "--format", "{{.Id}}")
    ensure_internal_network(limits.network_name)
    name = "teachingagent-faq-" + uuid.uuid4().hex
    proxy_name = name + "-proxy"
    token = secrets.token_urlsafe(32)
    container_id: str | None = None
    proxy_id: str | None = None
    workspace: RemoteWorkspace | None = None
    try:
        result = docker_command(
            "run",
            "--detach",
            "--pull=never",
            "--init",
            "--name",
            name,
            "--label",
            "teachingagent.managed=true",
            "--label",
            f"teachingagent.attempt={_digest_id(attempt_id)}",
            "--platform",
            config["platform"],
            "--network",
            limits.network_name,
            "--memory",
            f"{limits.memory_mb}m",
            "--memory-swap",
            f"{limits.memory_mb}m",
            "--cpus",
            str(limits.cpu_count),
            "--pids-limit",
            str(limits.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--env",
            f"OH_SESSION_API_KEYS_0={token}",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--mount",
            f"type=bind,source={directory},target=/workspace/student,readonly",
            image,
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        )
        container_id = result.stdout.strip()
        proxy = docker_command(
            "run",
            "--detach",
            "--pull=never",
            "--name",
            proxy_name,
            "--label",
            "teachingagent.managed=true",
            "--label",
            "teachingagent.role=fixed-agent-proxy",
            "--platform",
            config["platform"],
            "--network",
            "bridge",
            "--memory",
            "128m",
            "--memory-swap",
            "128m",
            "--cpus",
            "0.25",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--publish",
            "127.0.0.1::9000",
            "--entrypoint",
            "python",
            image,
            "-I",
            "-c",
            _fixed_tcp_proxy_command(name),
        )
        proxy_id = proxy.stdout.strip()
        docker_command("network", "connect", limits.network_name, proxy_id)
        ports = json.loads(
            docker_command(
                "inspect", "--format", "{{json .NetworkSettings.Ports}}", proxy_id
            ).stdout
        )
        binding = ports["9000/tcp"][0]
        if binding["HostIp"] != "127.0.0.1":
            raise RuntimeError("Workspace API must bind to localhost.")
        url = f"http://127.0.0.1:{binding['HostPort']}"
        deadline = time.monotonic() + limits.startup_timeout_seconds
        with httpx.Client(
            headers={"X-Session-API-Key": token}, timeout=3, trust_env=False
        ) as client:
            while True:
                try:
                    if client.get(url + "/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                running = docker_command(
                    "inspect", "--format", "{{.State.Running}}", container_id, check=False
                )
                proxy_running = docker_command(
                    "inspect", "--format", "{{.State.Running}}", proxy_id, check=False
                )
                if (
                    running.stdout.strip() != "true"
                    or proxy_running.stdout.strip() != "true"
                    or time.monotonic() >= deadline
                ):
                    logs = docker_command("logs", "--tail", "30", container_id, check=False)
                    proxy_logs = docker_command(
                        "logs", "--tail", "30", proxy_id, check=False
                    )
                    safe_logs = (
                        logs.stdout + logs.stderr + proxy_logs.stdout + proxy_logs.stderr
                    ).replace(token, "[REDACTED]")
                    raise RuntimeError("Agent Server failed to become ready: " + safe_logs)
                time.sleep(2)
        workspace = RemoteWorkspace(
            host=url,
            api_key=token,
            working_dir="/workspace/student",
            read_timeout=60,
        )
        yield RunningWorkspace(container_id, directory, url, workspace)
    finally:
        if workspace is not None:
            workspace.reset_client()
        if proxy_id:
            removed = docker_command("rm", "--force", proxy_id, check=False)
            if removed.returncode and "No such container" not in removed.stderr:
                raise RuntimeError("Workspace proxy cleanup failed: " + removed.stderr)
        if container_id:
            removed = docker_command("rm", "--force", container_id, check=False)
            if removed.returncode and "No such container" not in removed.stderr:
                raise RuntimeError("Workspace cleanup failed: " + removed.stderr)


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
