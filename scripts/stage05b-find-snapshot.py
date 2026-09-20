from hashlib import sha256
from pathlib import Path

attempt = "ef885ba8-cd80-4946-a7a0-65c23f303039"
snapshot_b = "90c3fbbb-7981-4de7-80fd-b7352756aa67"
digest = sha256(attempt.encode()).hexdigest()[:24]
root = Path(r"D:\TeachingAgent\workspaces\attempts") / f"attempt-{digest}"
print("attempt_dir", root)
print("exists", root.exists())
b = root / "snapshots" / snapshot_b
print("snapshot_b", b, b.exists())
if b.exists():
    for path in sorted(b.rglob("*")):
        if path.is_file():
            print(path.relative_to(b).as_posix(), path.stat().st_size)
