from __future__ import annotations

import os

from fastapi import FastAPI

from .business.api import router
from .business.database import create_business_engine, create_session_factory


def create_app(*, database_url: str | None = None, sqlite_test_mode: bool = False) -> FastAPI:
    app = FastAPI(title="AI 实训教练业务 API", version="0.2.0")
    url = database_url or os.environ.get("TEACHING_DATABASE_URL", "sqlite+aiosqlite:///./runtime/teaching.db")
    engine = create_business_engine(url, sqlite_test_mode=sqlite_test_mode or url.startswith("sqlite"))
    app.state.business_engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
