"""Deployment-only, idempotent LangGraph checkpoint table initialization."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.business.checkpoint import checkpoint_saver


async def main() -> None:
    conninfo = os.environ["LANGGRAPH_CHECKPOINT_DATABASE_URL"]
    async with checkpoint_saver(conninfo) as saver:
        await saver.setup()
    print("LangGraph checkpoint schema initialized")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
