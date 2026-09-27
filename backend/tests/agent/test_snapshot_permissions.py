"""Snapshot access for the unprivileged Python runner."""

import os
import stat
from pathlib import Path

import pytest

from app.agent.workspace import AttemptWorkspaceManager


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions are Linux-only")
def test_snapshot_is_readable_without_exposing_mutable_source(tmp_path: Path):
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    source = manager.source_directory("student-a")
    code = source / "main.py"
    code.write_text("print(42)\n", encoding="utf-8")
    source.chmod(0o700)
    code.chmod(0o600)

    snapshot, _ = manager.create_snapshot(attempt_id="student-a", snapshot_id="snapshot-a")

    assert stat.S_IMODE(source.stat().st_mode) == 0o700
    assert stat.S_IMODE(code.stat().st_mode) == 0o600
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o555
    assert stat.S_IMODE((snapshot / "main.py").stat().st_mode) == 0o444
    assert (snapshot / "main.py").read_text(encoding="utf-8") == "print(42)\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions are Linux-only")
def test_reusing_an_existing_snapshot_repairs_restrictive_modes(tmp_path: Path):
    manager = AttemptWorkspaceManager(tmp_path / "attempts", tmp_path / "evidence")
    manager.source_directory("student-a").joinpath("main.py").write_text("x = 1\n", encoding="utf-8")
    snapshot, reference = manager.create_snapshot(attempt_id="student-a", snapshot_id="snapshot-a")
    snapshot.chmod(0o700)
    (snapshot / "main.py").chmod(0o600)

    reused, reused_reference = manager.create_snapshot(attempt_id="student-a", snapshot_id="snapshot-a")

    assert reused == snapshot
    assert reused_reference == reference
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o555
    assert stat.S_IMODE((snapshot / "main.py").stat().st_mode) == 0o444
