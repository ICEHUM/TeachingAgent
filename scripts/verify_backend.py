from importlib import import_module, metadata
import sys

PACKAGES = {
    "openhands-sdk": "openhands.sdk",
    "openhands-tools": "openhands.tools.file_editor",
    "openhands-workspace": "openhands.workspace",
    "openhands-agent-server": "openhands.agent_server",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic-settings": "pydantic_settings",
    "httpx": "httpx",
    "sqlalchemy": "sqlalchemy",
    "aiosqlite": "aiosqlite",
}
print("Python:", sys.version)
failures = []
for distribution, module in PACKAGES.items():
    try:
        import_module(module)
        print(f"OK {distribution}=={metadata.version(distribution)}")
    except Exception as error:
        failures.append((distribution, repr(error)))
        print(f"FAIL {distribution}: {error!r}")

from fastapi import FastAPI
from fastapi.testclient import TestClient
app = FastAPI()
@app.get("/health")
def health():
    return {"status": "ok"}

with TestClient(app) as client:
    response = client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}
print("OK FastAPI HTTP smoke check (no external service or model call)")
if failures:
    raise SystemExit(1)
