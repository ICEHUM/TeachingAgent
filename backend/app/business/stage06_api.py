"""Stage 06 operational health surface for authorized teachers and demo admins."""

from __future__ import annotations

# Health probes intentionally collapse provider errors into bounded service states.
# ruff: noqa: BLE001
import asyncio
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

router = APIRouter(prefix="/api/product/teacher", tags=["stage06-operations"])
DEMO_COURSE_CODE = "DEMO-FAQ-001-RC06"
OperationalStatus = Literal["normal", "degraded", "unavailable"]


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
