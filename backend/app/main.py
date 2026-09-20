from __future__ import annotations

import os

from fastapi import FastAPI

from .business.api import router
from .business.database import create_business_engine, create_session_factory


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
    if enable_dev_auth and runtime_environment not in {"development", "dev", "test"}:
        raise RuntimeError("DEV_AUTH_ENABLED is forbidden outside DEV/TEST")
    app = FastAPI(title="AI 实训教练业务 API", version="0.2.0")
    url = database_url or os.environ.get("TEACHING_DATABASE_URL", "sqlite+aiosqlite:///./runtime/teaching.db")
    engine = create_business_engine(url, sqlite_test_mode=sqlite_test_mode or url.startswith("sqlite"))
    app.state.business_engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.dev_auth_enabled = enable_dev_auth
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
