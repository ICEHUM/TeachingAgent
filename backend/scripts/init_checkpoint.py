"""Deployment-only, idempotent LangGraph checkpoint table initialization."""

import asyncio
import os

from app.business.checkpoint import checkpoint_saver


async def main() -> None:
    conninfo = os.environ["LANGGRAPH_CHECKPOINT_DATABASE_URL"]
    async with checkpoint_saver(conninfo) as saver:
        await saver.setup()
    print("LangGraph checkpoint schema initialized")


if __name__ == "__main__":
    asyncio.run(main())
