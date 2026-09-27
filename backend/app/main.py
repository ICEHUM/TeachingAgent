from __future__ import annotations

import os

from fastapi import FastAPI

from .agent.interactive_runner import InteractivePythonManager
from .agent.runner import (
    DEFAULT_PYTHON_RUNNER_IMAGE,
    DockerPythonRunnerExecutor,
    TaskRoutingExecutor,
)
from .agent.teaching_llm import DeepSeekTeachingLLM
from .agent.tools import OpenHandsExecutor
from .agent.workspace import AttemptWorkspaceManager
from .business.api import router
from .business.course_builder import router as course_builder_router
from .business.database import create_business_engine, create_session_factory
from .business.registration import auth_router as registration_auth_router
from .business.registration import student_router as registration_student_router
from .business.registration import teacher_router as registration_teacher_router
from .business.stage03 import PersistentTeachingRuntime
from .business.stage05_api import router as product_router
from .business.stage06_api import auth_router as stage06_auth_router
from .business.stage06_api import router as stage06_router
from .business.teacher_assistant import router as teacher_assistant_router


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
    app.state.interactive_python = None
    checkpoint_url = (
        os.environ.get("LANGGRAPH_CHECKPOINT_DATABASE_URL")
        or os.environ.get("CHECKPOINT_DATABASE_URL")
    )
    if checkpoint_url:
        legacy_executor = OpenHandsExecutor(app.state.workspace_manager)
        runner_image = os.environ.get(
            "TEACHING_PYTHON_RUNNER_IMAGE", DEFAULT_PYTHON_RUNNER_IMAGE
        ).strip()
        python_executor = DockerPythonRunnerExecutor(runner_image, app.state.workspace_manager) if runner_image else None
        executor = TaskRoutingExecutor(legacy=legacy_executor, python=python_executor) if python_executor else legacy_executor
        if python_executor:
            app.state.interactive_python = InteractivePythonManager(python_executor)
            app.router.add_event_handler("shutdown", app.state.interactive_python.stop_all)
        runtime = PersistentTeachingRuntime(
            session_factory=session_factory,
            checkpoint_conninfo=checkpoint_url,
            executor=executor,
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
    app.include_router(registration_auth_router)
    app.include_router(registration_student_router)
    app.include_router(registration_teacher_router)
    app.include_router(teacher_assistant_router)
    app.include_router(course_builder_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
