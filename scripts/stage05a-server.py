"""Run the Stage 05A FastAPI server with Psycopg's Windows-compatible event loop."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


if __name__ == "__main__":
    os.environ.setdefault("TEACHING_ENV", "development")
    os.environ.setdefault("DEV_AUTH_ENABLED", "true")
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
        loop="none",
    )
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(server.serve())
