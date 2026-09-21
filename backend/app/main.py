from __future__ import annotations

import os

from fastapi import FastAPI

from .agent.teaching_llm import DeepSeekTeachingLLM
from .agent.tools import OpenHandsExecutor
from .agent.workspace import AttemptWorkspaceManager
from .business.api import router
from .business.database import create_business_engine, create_session_factory
from .business.stage03 import PersistentTeachingRuntime
from .business.stage05_api import router as product_router
from .business.stage06_api import auth_router as stage06_auth_router
from .business.stage06_api import router as stage06_router


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def create_app(
    *,
    database_url: str | None = None,
    sqlite_test_mode: bool = False,
    environment: str | None = None,
    dev_auth_enabled: bool | None = None,
) -> FastAPI:
    runtime_environment = (environment or os.environ.get("TEACHING_ENV", "development")).lower()
    enable_dev_auth = (
        _enabled(os.environ.get("DEV_AUTH_ENABLED"))
        if dev_auth_enabled is None
        else dev_auth_enabled
    )
    if enable_dev_auth and runtime_environment not in {"development", "dev", "test", "demo"}:
        raise RuntimeError("DEV_AUTH_ENABLED is forbidden outside DEV/TEST/DEMO")
    app = FastAPI(title="AI 实训教练业务 API", version="0.2.0")
    url = database_url or os.environ.get("TEACHING_DATABASE_URL", "sqlite+aiosqlite:///./runtime/teaching.db")
    engine = create_business_engine(url, sqlite_test_mode=sqlite_test_mode or url.startswith("sqlite"))
    app.state.business_engine = engine
    session_factory = create_session_factory(engine)
    app.state.session_factory = session_factory
    app.state.dev_auth_enabled = enable_dev_auth
    app.state.runtime_environment = runtime_environment
    app.state.workspace_manager = AttemptWorkspaceManager()
    checkpoint_url = (
        os.environ.get("LANGGRAPH_CHECKPOINT_DATABASE_URL")
        or os.environ.get("CHECKPOINT_DATABASE_URL")
    )
    if checkpoint_url:
        runtime = PersistentTeachingRuntime(
            session_factory=session_factory,
            checkpoint_conninfo=checkpoint_url,
            executor=OpenHandsExecutor(app.state.workspace_manager),
            llm=DeepSeekTeachingLLM(),
        )
        app.state.teaching_runtime = runtime
        app.state.graph_resume_runtime = runtime
    else:
        app.state.teaching_runtime = None
        app.state.graph_resume_runtime = None
    app.include_router(router)
    app.include_router(product_router)
    app.include_router(stage06_router)
    app.include_router(stage06_auth_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
