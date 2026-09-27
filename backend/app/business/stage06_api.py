"""Stage 06 operational health surface for authorized teachers and demo admins."""

from __future__ import annotations

# Health probes intentionally collapse provider errors into bounded service states.
# ruff: noqa: BLE001
import asyncio
import os
import secrets
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import ModelSettings
from app.agent.workspace import ROOT as PROJECT_ROOT
from app.agent.workspace import docker_command

from .api import current_user, db_session
from .models import Attempt, Course, CourseMembership, Task, TaskVersion, User
from .python_basics import COURSE_CODE as PYTHON_DEMO_COURSE_CODE

router = APIRouter(prefix="/api/product/teacher", tags=["stage06-operations"])
auth_router = APIRouter(prefix="/api/product/auth", tags=["stage06-demo-auth"])
DEMO_COURSE_CODE = "DEMO-FAQ-001-RC06"
OperationalStatus = Literal["normal", "degraded", "unavailable"]


class DemoLoginRequest(BaseModel):
    account: str
    password: str

    model_config = {"extra": "forbid"}


class DemoLoginResponse(BaseModel):
    role: Literal["student", "teacher"]
    display_name: str
    user_id: str
    attempt_id: str | None = None


def _demo_login_enabled(request: Request) -> bool:
    environment = str(getattr(request.app.state, "runtime_environment", "")).lower()
    return bool(getattr(request.app.state, "dev_auth_enabled", False)) and environment in {
        "test", "demo"
    }


@auth_router.post("/demo-login", response_model=DemoLoginResponse)
async def demo_login(
    payload: DemoLoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> DemoLoginResponse:
    """Resolve a local DEMO/TEST account into the real seeded business identity."""
    if not _demo_login_enabled(request):
        raise HTTPException(404, "demo_login_unavailable")

    student_account = os.environ.get("DEMO_STUDENT_ACCOUNT", "demo_student")
    teacher_account = os.environ.get("DEMO_TEACHER_ACCOUNT", "demo_teacher")
    teacher2_account = os.environ.get("DEMO_TEACHER2_ACCOUNT", "demo_teacher2")
    teacher3_account = os.environ.get("DEMO_TEACHER3_ACCOUNT", "demo_teacher3")
    credentials = {
        student_account: ("student", os.environ.get("DEMO_STUDENT_PASSWORD", ""), "student.rc06@demo.invalid"),
        teacher_account: ("teacher", os.environ.get("DEMO_TEACHER_PASSWORD", ""), "teacher.rc06@demo.invalid"),
        teacher2_account: ("teacher", os.environ.get("DEMO_TEACHER2_PASSWORD", "123456"), "teacher2.rc06@demo.invalid"),
        teacher3_account: ("teacher", os.environ.get("DEMO_TEACHER3_PASSWORD", "123456"), "teacher3.rc06@demo.invalid"),
    }
    configured = all(item[1] for item in credentials.values())
    if not configured:
        raise HTTPException(503, {"code": "demo_login_not_configured", "message": "演示登录尚未配置，请先运行演示初始化。"})
    selected = credentials.get(payload.account.strip())
    if selected is None or not secrets.compare_digest(payload.password, selected[1]):
        raise HTTPException(401, {"code": "invalid_credentials", "message": "账号或密码不正确。"})

    role, _password, email = selected
    user = await session.scalar(select(User).where(User.email == email))
    course = await session.scalar(select(Course).where(Course.code == DEMO_COURSE_CODE))
    if user is None or course is None:
        raise HTTPException(503, {"code": "demo_environment_not_initialized", "message": "演示数据尚未初始化。"})
    membership = await session.scalar(select(CourseMembership).where(
        CourseMembership.course_id == course.id,
        CourseMembership.user_id == user.id,
        CourseMembership.role == role,
    ))
    if membership is None or user.system_role != role:
        raise HTTPException(403, "demo_membership_invalid")

    attempt_id: str | None = None
    if role == "student":
        python_course = await session.scalar(select(Course).where(Course.code == PYTHON_DEMO_COURSE_CODE))
        selected_course_id = python_course.id if python_course is not None else course.id
        attempt_id = await session.scalar(
            select(Attempt.id)
            .join(TaskVersion, Attempt.task_version_id == TaskVersion.id)
            .join(Task, TaskVersion.task_id == Task.id)
            .where(Task.course_id == selected_course_id, Attempt.learner_id == user.id)
            .order_by(Task.task_key.asc(), Attempt.created_at.desc())
            .limit(1)
        )
        if attempt_id is None:
            raise HTTPException(503, {"code": "demo_attempt_not_initialized", "message": "学生演示任务尚未初始化。"})
    return DemoLoginResponse(role=role, display_name=user.display_name, user_id=user.id, attempt_id=attempt_id)


class DemoHealth(BaseModel):
    business_database: OperationalStatus
    execution_environment: OperationalStatus
    teaching_flow: OperationalStatus
    model_service: OperationalStatus


async def _execution_status() -> OperationalStatus:
    try:
        config = __import__("json").loads((PROJECT_ROOT / "workspaces" / "agent-server-image.json").read_text(encoding="utf-8"))
        result = await asyncio.to_thread(
            docker_command, "image", "inspect", config["server_image"],
            "--format", "{{.Id}}", timeout=8, check=False,
        )
        return "normal" if result.returncode == 0 else "unavailable"
    except Exception:
        return "unavailable"


async def _model_status() -> OperationalStatus:
    try:
        settings = ModelSettings()
        key = settings.llm_api_key.get_secret_value()
        if not key:
            return "degraded"
        async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
            response = await client.get(
                settings.llm_base_url.rstrip("/") + "/models",
                headers={"Authorization": "Bearer " + key},
            )
        return "normal" if response.status_code < 500 else "degraded"
    except Exception:
        return "degraded"


@router.get("/demo-health", response_model=DemoHealth)
async def demo_health(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(current_user)],
) -> DemoHealth:
    if user.system_role not in {"teacher", "admin"}:
        raise HTTPException(403, "teacher_role_required")
    course = await session.scalar(select(Course).where(Course.code == DEMO_COURSE_CODE))
    if course is None:
        raise HTTPException(404, "demo_environment_not_initialized")
    membership = await session.scalar(select(CourseMembership).where(
        CourseMembership.course_id == course.id,
        CourseMembership.user_id == user.id,
        CourseMembership.role.in_(["teacher", "owner"]),
    ))
    if membership is None and user.system_role != "admin":
        raise HTTPException(403, "course_membership_required")
    attempt = await session.scalar(
        select(Attempt)
        .join(TaskVersion, Attempt.task_version_id == TaskVersion.id)
        .join(Task, TaskVersion.task_id == Task.id)
        .where(Task.course_id == course.id)
        .limit(1)
    )
    flow_status: OperationalStatus
    if attempt is None:
        flow_status = "unavailable"
    elif getattr(request.app.state, "teaching_runtime", None) is None:
        flow_status = "degraded"
    else:
        flow_status = "normal"
    execution_status, model_status = await asyncio.gather(
        _execution_status(), _model_status()
    )
    return DemoHealth(
        business_database="normal",
        execution_environment=execution_status,
        teaching_flow=flow_status,
        model_service=model_status,
    )
