from __future__ import annotations

import httpx
import pytest

from app.agent.llm import ModelSettings
from app.business.models import (
    Attempt,
    Base,
    Course,
    CourseMembership,
    RequirementDefinition,
    RequirementResult,
    Snapshot,
    Task,
    TaskStage,
    TaskVersion,
    User,
)
from app.business.service import BusinessService
from app.business.teacher_assistant import ClassroomQuery, TeacherAssistantModel, _chart
from app.main import create_app


class FakeTeacherModel:
    def __init__(self, answer: str = "根据当前证据，优先查看需排查学生。", query: ClassroomQuery | None = None) -> None:
        self.answer = answer
        self.query = query or ClassroomQuery()
        self.messages: list[dict[str, str]] = []
        self.plan_messages: list[dict[str, str]] = []

    async def plan(self, messages: list[dict[str, str]]) -> ClassroomQuery:
        self.plan_messages = messages
        return self.query

    async def complete(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return self.answer


class BrokenTeacherModel(FakeTeacherModel):
    async def complete(self, messages: list[dict[str, str]]) -> str:
        raise RuntimeError("provider timeout")


class FakeStreamingTeacherModel:
    """Streams a natural answer after choosing a query."""

    def __init__(self, chunks: list[str], query: ClassroomQuery | None = None) -> None:
        self.chunks = chunks
        self.query = query or ClassroomQuery()
        self.messages: list[dict[str, str]] = []

    async def plan(self, messages: list[dict[str, str]]) -> ClassroomQuery:
        return self.query

    async def stream(self, messages: list[dict[str, str]]):
        self.messages = messages
        for chunk in self.chunks:
            yield chunk


class InterruptedStreamModel(FakeTeacherModel):
    async def stream(self, messages: list[dict[str, str]]):
        raise httpx.ReadTimeout("temporary stream interruption")
        yield "unreachable"


async def _app(tmp_path):
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'assistant.db').as_posix()}",
        sqlite_test_mode=True,
        environment="test",
        dev_auth_enabled=True,
    )
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.state.session_factory() as session:
        teacher = User(id="assistant-teacher", email="teacher@test", display_name="教师", system_role="teacher")
        outsider = User(id="assistant-outsider", email="outsider@test", display_name="外部教师", system_role="teacher")
        first = User(id="assistant-student-1", email="s1@test", display_name="学生一")
        second = User(id="assistant-student-2", email="s2@test", display_name="学生二")
        course = Course(id="assistant-course", code="ASSIST", name="助手测试课")
        session.add_all([teacher, outsider, first, second, course])
        session.add_all([
            CourseMembership(course_id=course.id, user_id=teacher.id, role="teacher"),
            CourseMembership(course_id=course.id, user_id=first.id, role="student"),
            CourseMembership(course_id=course.id, user_id=second.id, role="student"),
        ])
        await session.commit()
        service = BusinessService()
        version = await service.create_faq_version(session, course_id=course.id, actor_id=teacher.id)
        await service.create_attempt(session, task_version_id=version.id, learner_id=first.id, mode="guided_practice")
        await service.create_attempt(session, task_version_id=version.id, learner_id=first.id, mode="guided_practice")
        await service.create_attempt(session, task_version_id=version.id, learner_id=second.id, mode="guided_practice")
    return app


@pytest.mark.asyncio
async def test_teacher_context_requires_membership_and_lists_real_course(tmp_path):
    app = await _app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.get("/api/product/teacher/assistant/context", headers={"X-User-Id": "assistant-teacher"})
        assert allowed.status_code == 200
        body = allowed.json()
        assert body["courses"] == [{"id": "assistant-course", "name": "助手测试课", "code": "ASSIST"}]
        assert body["image_available"] is False
        denied = await client.get("/api/product/teacher/assistant/context", headers={"X-User-Id": "assistant-outsider"})
        assert denied.status_code == 200
        assert denied.json()["courses"] == []
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_message_uses_deduplicated_attempts_and_server_chart(tmp_path):
    app = await _app(tmp_path)
    model = FakeTeacherModel(query=ClassroomQuery(kind="bar", group_by="status", title="状态分布"))
    app.state.teacher_assistant_model = model
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "分析班级当前情况"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"]["student_count"] == 2
        assert body["source"]["attempt_count"] == 2
        assert body["chart"]["kind"] == "bar"
        assert sum(body["chart"]["values"]) == 2
        assert body["model_used"] is True
        assert "数据库证据" in model.messages[-1]["content"]
        assert "本次问题：分析班级当前情况" in model.plan_messages[-1]["content"]
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_teacher_answer_exposes_observed_python_skill_counts(tmp_path):
    app = await _app(tmp_path)
    async with app.state.session_factory() as session:
        task = Task(id="assistant-python-task", course_id="assistant-course", task_key="PYB-02", title="判断是否及格")
        version = TaskVersion(id="assistant-python-version", task_id=task.id, version="v1")
        stage = TaskStage(id="assistant-python-stage", task_version_id=version.id,
                          stage_key="write_program", title="编写与验证", position=0)
        requirement = RequirementDefinition(
            id="assistant-python-public", task_stage_id=stage.id,
            requirement_key="passing_public_tests", kind="AUTO_TEST",
            evaluator="python_cases_v1", config={},
        )
        passed = Attempt(id="assistant-python-passed", task_version_id=version.id,
                         learner_id="assistant-student-1", current_stage_id=stage.id)
        unchecked = Attempt(id="assistant-python-unchecked", task_version_id=version.id,
                            learner_id="assistant-student-2", current_stage_id=stage.id)
        snapshot = Snapshot(id="assistant-python-snapshot", attempt_id=passed.id,
                            snapshot_ref="sha256:assistant-python", sequence=1)
        result = RequirementResult(
            id="assistant-python-result", attempt_id=passed.id, requirement_id=requirement.id,
            snapshot_id=snapshot.id, operation_id="assistant-python-check", status="SATISFIED",
            evaluator="python_cases_v1", evidence_refs=[], version=1,
        )
        session.add_all([task, version, stage, requirement, passed, unchecked, snapshot, result])
        await session.commit()

    model = FakeTeacherModel()
    app.state.teacher_assistant_model = model
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "判断是否及格这题检查如何？"},
        )
    assert response.status_code == 200
    assert response.json()["source"]["skill_evidence"] == [{
        "task_key": "PYB-02", "task_title": "判断是否及格",
        "skill_ids": ["if_else", "comparison_boundary"],
        "public_passed": 1, "public_failed": 0, "not_checked": 1,
    }]
    assert "技能证据仅统计公开检查状态" in model.messages[0]["content"]
    assert '"public_passed": 1' in model.messages[-1]["content"]
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_model_failure_is_explicit_and_does_not_fabricate_answer(tmp_path):
    app = await _app(tmp_path)
    app.state.teacher_assistant_model = BrokenTeacherModel()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "请分析"},
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "teacher_assistant_unavailable"
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_prompt_asks_for_natural_markdown_instead_of_json(tmp_path):
    app = await _app(tmp_path)
    model = FakeTeacherModel()
    app.state.teacher_assistant_model = model
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "这节课怎么安排"},
        )
        assert response.status_code == 200
    system = model.messages[0]["content"]
    assert "不要输出 JSON" in system
    assert "只输出JSON对象" not in system
    assert "不得猜测某个学生失败的代码原因" in system
    assert "不能称它为单维度图" in system
    assert "继续讨论" not in response.json()
    await app.state.business_engine.dispose()


def test_two_dimension_bar_displays_actual_public_check_groups():
    rows = [
        {"student_id": "a", "student_name": "甲", "attempt_id": "a-1", "task_key": "PYB-01",
         "task_title": "两数相加", "public_check": "通过"},
        {"student_id": "b", "student_name": "乙", "attempt_id": "b-1", "task_key": "PYB-01",
         "task_title": "两数相加", "public_check": "未通过"},
        {"student_id": "c", "student_name": "丙", "attempt_id": "c-2", "task_key": "PYB-02",
         "task_title": "判断是否及格", "public_check": "通过"},
    ]
    query = ClassroomQuery(scope="attempts", kind="bar", group_by="task", split_by="public_check",
                           title="各练习公开检查")

    chart = _chart({}, query, rows)

    assert chart is not None
    assert dict(zip(chart["categories"], chart["values"])) == {
        "两数相加 · 通过": 1,
        "两数相加 · 未通过": 1,
        "判断是否及格 · 通过": 1,
    }
    assert sum(len(group) for group in chart["students"]) == 3


@pytest.mark.asyncio
async def test_model_selected_query_creates_chart_without_control_tag(tmp_path):
    app = await _app(tmp_path)
    app.state.teacher_assistant_model = FakeTeacherModel("明细如下：", ClassroomQuery(kind="table", title="学生进度明细"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "列一下进度"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["chart"]["kind"] == "table"
        assert body["answer"] == "明细如下："
        assert len(body["chart"]["rows"]) == 2
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_stream_sends_model_selected_chart_deltas_and_done(tmp_path):
    app = await _app(tmp_path)
    model = FakeStreamingTeacherModel(["先看状态分布，", "再安排下一步。"], ClassroomQuery(kind="bar", group_by="status", title="状态分布"))
    app.state.teacher_assistant_model = model
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages/stream",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "分析班级当前情况"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = response.text
    assert "event: meta" in text
    assert "event: chart" in text
    assert '"kind": "bar"' in text or '"kind":"bar"' in text
    assert text.count("event: delta") >= 2
    assert "先看状态分布，" in text
    assert "event: done" in text
    assert '"model_used": true' in text
    assert "数据库证据" in model.messages[-1]["content"]
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_stream_reports_error_after_model_selected_chart_when_model_is_unavailable(tmp_path):
    app = await _app(tmp_path)
    app.state.teacher_assistant_model = BrokenTeacherModel(query=ClassroomQuery(kind="bar", group_by="status"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages/stream",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "用柱状图看当前状态"},
        )
        assert response.status_code == 200
        text = response.text
    assert "event: chart" in text
    assert "event: error" in text
    assert "event: done" not in text
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_stream_reports_unavailable_without_inventing_an_answer(tmp_path):
    app = await _app(tmp_path)
    app.state.teacher_assistant_model = BrokenTeacherModel()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages/stream",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "随便聊聊教学"},
        )
        assert response.status_code == 200
        text = response.text
    assert "event: error" in text
    assert "teacher_assistant_unavailable" in text
    assert "event: done" not in text
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_stream_recovers_with_live_model_completion_after_early_interruption(tmp_path):
    app = await _app(tmp_path)
    app.state.teacher_assistant_model = InterruptedStreamModel("已经恢复回答。")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages/stream",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "如何安排课堂？"},
        )
    assert response.status_code == 200
    assert "已经恢复回答。" in response.text
    assert "event: done" in response.text
    assert "event: error" not in response.text
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_chart_is_not_selected_by_keywords_or_client_flags(tmp_path):
    app = await _app(tmp_path)
    app.state.teacher_assistant_model = FakeTeacherModel(query=ClassroomQuery(kind="none"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        plain = await client.post(
            "/api/product/teacher/assistant/messages",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "请用桑基图展示数据"},
        )
        forced = await client.post(
            "/api/product/teacher/assistant/messages",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "你好", "chart_kind": "sankey"},
        )
    assert plain.status_code == 200
    assert plain.json()["chart"] is None
    assert forced.status_code == 422
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_provider_timeout_is_reported_by_model_adapter():
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout")

    adapter = TeacherAssistantModel(
        settings=ModelSettings(
            llm_model="deepseek-chat",
            llm_api_key="test-key",
            llm_base_url="https://model.test",
            llm_provider="deepseek",
        ),
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(httpx.ReadTimeout):
        await adapter.complete([{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_all_chart_kinds_use_common_real_data_contract(tmp_path):
    app = await _app(tmp_path)
    model = FakeTeacherModel()
    app.state.teacher_assistant_model = model
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for kind in ("sankey", "bar", "line", "table", "heatmap"):
            model.query = ClassroomQuery(kind=kind, group_by="created_date" if kind == "line" else "stage", split_by="status" if kind in {"sankey", "heatmap"} else "none", title=f"自选{kind}")
            response = await client.post(
                "/api/product/teacher/assistant/messages",
                headers={"X-User-Id": "assistant-teacher"},
                json={"course_id": "assistant-course", "message": "查看数据"},
            )
            assert response.status_code == 200
            chart = response.json()["chart"]
            assert chart["kind"] == kind
            assert chart["title"] and chart["description"]
            if kind == "bar":
                assert len(chart["categories"]) == len(chart["values"]) == len(chart["students"])
            elif kind == "line":
                assert len(chart["categories"]) == len(chart["students"])
                assert all(len(series["values"]) == len(chart["categories"]) for series in chart["series"])
            elif kind == "table":
                assert len(chart["rows"]) == len(chart["students"])
            elif kind == "heatmap":
                assert all({"x", "y", "value", "students"} <= set(cell) for cell in chart["cells"])
            else:
                assert all({"source", "target", "value", "students"} <= set(link) for link in chart["links"])
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_free_conversation_without_chart_request_returns_plain_answer(tmp_path):
    app = await _app(tmp_path)
    app.state.teacher_assistant_model = FakeTeacherModel()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "帮我出几道适合高职学生的 Python 字典练习题"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["chart"] is None
        assert body["model_used"] is True
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_chinese_view_request_selects_server_chart_without_client_flag(tmp_path):
    app = await _app(tmp_path)
    app.state.teacher_assistant_model = FakeTeacherModel(query=ClassroomQuery(kind="bar", group_by="status", title="状态分布"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/product/teacher/assistant/messages",
            headers={"X-User-Id": "assistant-teacher"},
            json={"course_id": "assistant-course", "message": "请用柱状图分析当前状态"},
        )
        assert response.status_code == 200
        assert response.json()["chart"]["kind"] == "bar"
    await app.state.business_engine.dispose()
