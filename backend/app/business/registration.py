"""Local trial registration and teacher-managed class enrollment.

This surface deliberately shares the DEMO/TEST identity boundary. A production
deployment needs session authentication before enabling public registration.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .api import current_user, db_session
from .models import (
    AccountCredential,
    Attempt,
    ClassEnrollment,
    ClassGroup,
    Course,
    CourseMembership,
    Task,
    TaskStage,
    TaskVersion,
    User,
)
from .python_basics import FILE_NAME, TASK_PACKS
from .stage06_api import DemoLoginRequest, DemoLoginResponse, _demo_login_enabled, demo_login

auth_router = APIRouter(prefix="/api/product/auth", tags=["registration"])
teacher_router = APIRouter(prefix="/api/product/teacher", tags=["classes"])
student_router = APIRouter(prefix="/api/product/student", tags=["classes"])
HASH_ROUNDS = 310_000
ACCOUNT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{3,39}$")


def _trial_only(request: Request) -> None:
    if not _demo_login_enabled(request):
        raise HTTPException(404, "registration_unavailable")


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ROUNDS).hex()


class RegisterBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str = Field(min_length=4, max_length=40)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(min_length=2, max_length=120)

    @field_validator("account")
    @classmethod
    def clean_account(cls, value: str) -> str:
        value = value.strip().lower()
        if not ACCOUNT_PATTERN.fullmatch(value):
            raise ValueError("账号须以字母开头，只能使用 4–40 位小写字母、数字或下划线")
        return value

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("姓名至少需要两个字")
        return value


class StudentRegistration(RegisterBase):
    class_id: str = Field(min_length=1)


class CreateClass(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    course_id: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("班级名称至少需要两个字")
        return value


class BindCourse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: str


async def _credential_available(session: AsyncSession, account: str) -> None:
    reserved = {
        os.environ.get("DEMO_STUDENT_ACCOUNT", "demo_student"),
        os.environ.get("DEMO_TEACHER_ACCOUNT", "demo_teacher"),
        os.environ.get("DEMO_TEACHER2_ACCOUNT", "demo_teacher2"),
        os.environ.get("DEMO_TEACHER3_ACCOUNT", "demo_teacher3"),
    }
    if account in reserved or await session.scalar(select(AccountCredential.id).where(AccountCredential.account == account)):
        raise _error(409, "account_taken", "这个账号已被使用，请换一个。")


async def _create_user(session: AsyncSession, body: RegisterBase, role: str) -> User:
    await _credential_available(session, body.account)
    user = User(email=f"{body.account}@registered.local.invalid", display_name=body.display_name, system_role=role)
    salt = secrets.token_bytes(16)
    session.add(user)
    await session.flush()
    session.add(AccountCredential(user_id=user.id, account=body.account,
                                  password_salt=salt.hex(), password_hash=_password_hash(body.password, salt)))
    return user


async def _enroll_course(session: AsyncSession, student: User, course_id: str) -> list[tuple[Attempt, str, str]]:
    membership = await session.scalar(select(CourseMembership.id).where(
        CourseMembership.course_id == course_id, CourseMembership.user_id == student.id))
    if not membership:
        session.add(CourseMembership(course_id=course_id, user_id=student.id, role="student"))
    rows = (await session.execute(
        select(TaskVersion, Task, TaskStage)
        .join(Task, TaskVersion.task_id == Task.id)
        .join(TaskStage, TaskStage.task_version_id == TaskVersion.id)
        .where(Task.course_id == course_id, TaskVersion.status == "published",
               Task.task_key.in_(list(TASK_PACKS)))
        .order_by(Task.task_key, TaskVersion.created_at.desc(), TaskStage.position)
    )).all()
    created: list[tuple[Attempt, str, str]] = []
    seen: set[str] = set()
    for version, task, stage in rows:
        if task.id in seen:
            continue
        seen.add(task.id)
        existing = await session.scalar(select(Attempt.id).where(
            Attempt.learner_id == student.id, Attempt.task_version_id == version.id))
        if existing:
            continue
        attempt = Attempt(task_version_id=version.id, learner_id=student.id,
                          current_stage_id=stage.id, mode="guided_practice", status="active")
        session.add(attempt)
        await session.flush()
        created.append((attempt, task.task_key, task.title))
    return created


def _prepare_workspaces(request: Request, created: list[tuple[Attempt, str, str]]) -> None:
    manager = request.app.state.workspace_manager
    for attempt, key, title in created:
        source = manager.source_directory(attempt.id)
        starter = TASK_PACKS.get(key)
        path = source / FILE_NAME
        if not path.exists():
            path.write_text(starter.starter if starter else "# 在这里编写你的 Python 程序\n", encoding="utf-8")
        readme = source / "README.md"
        if not readme.exists():
            readme.write_text(f"# {title}\n\n在 `{FILE_NAME}` 中完成程序。\n", encoding="utf-8")


def _login_result(user: User, attempt_id: str | None = None) -> DemoLoginResponse:
    return DemoLoginResponse(role=user.system_role, display_name=user.display_name,
                             user_id=user.id, attempt_id=attempt_id)


@auth_router.post("/register/teacher", response_model=DemoLoginResponse, status_code=201)
async def register_teacher(body: RegisterBase, request: Request,
                           session: Annotated[AsyncSession, Depends(db_session)]) -> DemoLoginResponse:
    _trial_only(request)
    try:
        user = await _create_user(session, body, "teacher")
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(409, "account_taken", "这个账号已被使用，请换一个。") from exc
    return _login_result(user)


@auth_router.post("/register/student", response_model=DemoLoginResponse, status_code=201)
async def register_student(body: StudentRegistration, request: Request,
                           session: Annotated[AsyncSession, Depends(db_session)]) -> DemoLoginResponse:
    _trial_only(request)
    classroom = await session.get(ClassGroup, body.class_id)
    if classroom is None:
        raise _error(404, "class_not_found", "班级不存在，请刷新列表后重新选择。")
    try:
        user = await _create_user(session, body, "student")
        session.add(ClassEnrollment(class_id=classroom.id, student_id=user.id))
        created = await _enroll_course(session, user, classroom.course_id) if classroom.course_id else []
        _prepare_workspaces(request, created)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(409, "account_taken", "这个账号已被使用，请换一个。") from exc
    return _login_result(user, created[0][0].id if created else None)


@auth_router.post("/login", response_model=DemoLoginResponse)
async def login(body: DemoLoginRequest, request: Request,
                session: Annotated[AsyncSession, Depends(db_session)]) -> DemoLoginResponse:
    _trial_only(request)
    account = body.account.strip().lower()
    credential = await session.scalar(select(AccountCredential).where(AccountCredential.account == account))
    if credential is None:
        return await demo_login(body, request, session)
    expected = _password_hash(body.password, bytes.fromhex(credential.password_salt))
    if not secrets.compare_digest(expected, credential.password_hash):
        raise _error(401, "invalid_credentials", "账号或密码不正确。")
    user = await session.get(User, credential.user_id)
    if user is None:
        raise _error(401, "invalid_credentials", "账号或密码不正确。")
    attempt_id = None
    if user.system_role == "student":
        attempt_id = await session.scalar(select(Attempt.id).where(Attempt.learner_id == user.id)
                                          .order_by(Attempt.created_at).limit(1))
    return _login_result(user, attempt_id)


@auth_router.get("/classes")
async def open_classes(request: Request, session: Annotated[AsyncSession, Depends(db_session)]):
    _trial_only(request)
    rows = (await session.execute(
        select(ClassGroup, User.display_name, Course.name)
        .join(User, User.id == ClassGroup.teacher_id)
        .outerjoin(Course, Course.id == ClassGroup.course_id)
        .order_by(ClassGroup.created_at.desc(), ClassGroup.name)
    )).all()
    return {"classes": [{"id": group.id, "name": group.name,
                          "teacher_name": teacher_name, "course_name": course_name}
                         for group, teacher_name, course_name in rows]}


async def _owned_class(session: AsyncSession, user: User, class_id: str) -> ClassGroup:
    if user.system_role not in {"teacher", "admin"}:
        raise _error(403, "teacher_role_required", "只有教师可以管理班级。")
    group = await session.get(ClassGroup, class_id)
    if group is None or group.teacher_id != user.id:
        raise _error(404, "class_not_found", "找不到这个班级。")
    return group


async def _teacher_course(session: AsyncSession, user: User, course_id: str) -> None:
    membership = await session.scalar(select(CourseMembership.id).where(
        CourseMembership.course_id == course_id, CourseMembership.user_id == user.id,
        CourseMembership.role.in_(["teacher", "owner"])))
    if membership is None:
        raise _error(403, "course_access_denied", "只能选择自己负责的课程。")


@teacher_router.get("/classes")
async def teacher_classes(session: Annotated[AsyncSession, Depends(db_session)],
                          user: Annotated[User, Depends(current_user)]):
    if user.system_role not in {"teacher", "admin"}:
        raise _error(403, "teacher_role_required", "只有教师可以查看班级。")
    groups = list((await session.scalars(select(ClassGroup).where(ClassGroup.teacher_id == user.id)
                                        .order_by(ClassGroup.created_at.desc()))).all())
    data = []
    for group in groups:
        course = await session.get(Course, group.course_id) if group.course_id else None
        students = (await session.execute(
            select(User.id, User.display_name, AccountCredential.account)
            .join(ClassEnrollment, ClassEnrollment.student_id == User.id)
            .outerjoin(AccountCredential, AccountCredential.user_id == User.id)
            .where(ClassEnrollment.class_id == group.id)
            .order_by(ClassEnrollment.created_at)
        )).all()
        data.append({"id": group.id, "name": group.name, "course_id": group.course_id,
                     "course_name": course.name if course else None,
                     "students": [{"id": id_, "display_name": name, "account": account}
                                  for id_, name, account in students]})
    return {"classes": data}


@teacher_router.post("/classes", status_code=201)
async def create_class(body: CreateClass, session: Annotated[AsyncSession, Depends(db_session)],
                       user: Annotated[User, Depends(current_user)]):
    if user.system_role not in {"teacher", "admin"}:
        raise _error(403, "teacher_role_required", "只有教师可以创建班级。")
    if body.course_id:
        await _teacher_course(session, user, body.course_id)
    group = ClassGroup(teacher_id=user.id, course_id=body.course_id, name=body.name)
    session.add(group)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(409, "class_name_taken", "你已有同名班级，请换一个名称。") from exc
    return {"id": group.id, "name": group.name, "course_id": group.course_id}


@teacher_router.put("/classes/{class_id}/course")
async def bind_class_course(class_id: str, body: BindCourse, request: Request,
                            session: Annotated[AsyncSession, Depends(db_session)],
                            user: Annotated[User, Depends(current_user)]):
    group = await _owned_class(session, user, class_id)
    await _teacher_course(session, user, body.course_id)
    if group.course_id and group.course_id != body.course_id:
        raise _error(409, "course_already_bound", "班级已有课程，不能直接更换以免混淆学生记录。")
    group.course_id = body.course_id
    students = (await session.scalars(select(User).join(ClassEnrollment, ClassEnrollment.student_id == User.id)
                                      .where(ClassEnrollment.class_id == group.id))).all()
    created: list[tuple[Attempt, str, str]] = []
    for student in students:
        created.extend(await _enroll_course(session, student, body.course_id))
    _prepare_workspaces(request, created)
    await session.commit()
    return {"id": group.id, "course_id": group.course_id, "assigned_attempts": len(created)}


@student_router.get("/classes")
async def student_classes(session: Annotated[AsyncSession, Depends(db_session)],
                          user: Annotated[User, Depends(current_user)]):
    if user.system_role != "student":
        raise _error(403, "student_role_required", "只有学生可以查看已加入班级。")
    rows = (await session.execute(select(ClassGroup.name, Course.name, User.display_name)
        .join(ClassEnrollment, ClassEnrollment.class_id == ClassGroup.id)
        .join(User, User.id == ClassGroup.teacher_id)
        .outerjoin(Course, Course.id == ClassGroup.course_id)
        .where(ClassEnrollment.student_id == user.id))).all()
    return {"classes": [{"name": group, "course_name": course,
                          "teacher_name": teacher} for group, course, teacher in rows]}
