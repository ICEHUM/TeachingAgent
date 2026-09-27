from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from app.business.models import Base, User
from app.main import create_app


class FakeQuestionModel:
    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid
        self.messages: list[dict[str, str]] = []

    async def complete(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        if self.invalid:
            return "这些题目很好，但不是 JSON。"
        return json.dumps({"questions": [
            {
                "title": f"循环求和练习 {index}",
                "objective": "通过 for 循环遍历列表并累加整数。",
                "description": "给定整数列表 numbers，使用 for 循环计算总和并打印结果。空列表输出 0。",
                "starter_code": "numbers = [1, 2, 3]\ntotal = 0\n# 补全循环\n",
                "sample_input": "",
                "sample_output": "6",
                "answer_outline": "初始化累加变量，逐项相加后输出。",
            }
            for index in (1, 2, 3)
        ]}, ensure_ascii=False)


def docx_bytes(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
        ))
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_teacher_creates_course_uploads_design_and_reviews_ai_drafts(tmp_path):
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'courses.db').as_posix()}",
        sqlite_test_mode=True, environment="test", dev_auth_enabled=True,
    )
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.state.session_factory() as session:
        session.add_all([
            User(id="course-teacher-one", email="teacher-one@test", display_name="教师一", system_role="teacher"),
            User(id="course-teacher-two", email="teacher-two@test", display_name="教师二", system_role="teacher"),
            User(id="course-student", email="student@test", display_name="学生", system_role="student"),
        ])
        await session.commit()
    model = FakeQuestionModel()
    app.state.question_generator_model = model
    owner = {"X-User-Id": "course-teacher-one"}
    other = {"X-User-Id": "course-teacher-two"}
    student = {"X-User-Id": "course-student"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/product/teacher/courses", headers=student,
                                  json={"name": "Python 程序设计"})).status_code == 403
        created = await client.post("/api/product/teacher/courses", headers=owner,
                                    json={"name": "  Python 程序设计  "})
        assert created.status_code == 201, created.text
        course = created.json()
        assert course["name"] == "Python 程序设计"
        assert course["role"] == "owner"
        assert [item["id"] for item in (await client.get("/api/product/teacher/courses", headers=owner)).json()["courses"]] == [course["id"]]
        assert (await client.get("/api/product/teacher/courses", headers=other)).json()["courses"] == []

        source = "课程目标：掌握 Python 基础语法、列表与 for 循环。教学内容：遍历列表，对整数求和，并能处理空列表。"
        uploaded = await client.post(f"/api/product/teacher/courses/{course['id']}/designs",
                                     headers=owner, files={"file": ("课程设计.docx", docx_bytes(source))})
        assert uploaded.status_code == 201, uploaded.text
        design = uploaded.json()
        assert design["file_name"] == "课程设计.docx"
        assert "遍历列表" in design["preview"]
        assert (await client.post(f"/api/product/teacher/courses/{course['id']}/designs",
                                  headers=other, files={"file": ("a.txt", source.encode())})).status_code == 403
        downloaded = await client.get(
            f"/api/product/teacher/courses/{course['id']}/designs/{design['id']}/download", headers=owner,
        )
        assert downloaded.content == docx_bytes(source)

        generated = await client.post(f"/api/product/teacher/courses/{course['id']}/generate-questions",
                                      headers=owner, json={"design_id": design["id"], "count": 3})
        assert generated.status_code == 201, generated.text
        drafts = generated.json()["drafts"]
        assert len(drafts) == 3
        assert all(item["status"] == "draft" for item in drafts)
        assert drafts[0]["starter_code"].endswith("# 补全循环\n")
        assert "遍历列表" in model.messages[-1]["content"]
        assert (await client.post(f"/api/product/teacher/courses/{course['id']}/generate-questions",
                                  headers=other, json={"design_id": design["id"], "count": 3})).status_code == 403
        assert (await client.get(f"/api/product/teacher/courses/{course['id']}/question-drafts",
                                 headers=other)).status_code == 403

        edited = {key: drafts[0][key] for key in (
            "title", "objective", "description", "starter_code", "sample_input", "sample_output", "answer_outline",
        )}
        edited["title"] = "遍历列表并求和"
        assert (await client.put(f"/api/product/teacher/question-drafts/{drafts[0]['id']}",
                                 headers=other, json=edited)).status_code == 403
        saved = await client.put(f"/api/product/teacher/question-drafts/{drafts[0]['id']}",
                                 headers=owner, json=edited)
        assert saved.status_code == 200
        assert saved.json()["title"] == "遍历列表并求和"
        assert saved.json()["starter_code"].endswith("# 补全循环\n")
        discarded = await client.post(f"/api/product/teacher/question-drafts/{drafts[1]['id']}/discard",
                                      headers=owner)
        assert discarded.json()["status"] == "discarded"
        listed = await client.get(f"/api/product/teacher/courses/{course['id']}/question-drafts", headers=owner)
        assert len(listed.json()["drafts"]) == 2
    await app.state.business_engine.dispose()


@pytest.mark.asyncio
async def test_generation_failure_does_not_create_fake_questions(tmp_path):
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'generation.db').as_posix()}",
        sqlite_test_mode=True, environment="test", dev_auth_enabled=True,
    )
    async with app.state.business_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.state.session_factory() as session:
        session.add(User(id="course-failure-teacher", email="failure@test", display_name="教师", system_role="teacher"))
        await session.commit()
    app.state.question_generator_model = FakeQuestionModel(invalid=True)
    headers = {"X-User-Id": "course-failure-teacher"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        course = (await client.post("/api/product/teacher/courses", headers=headers,
                                    json={"name": "Python 基础课"})).json()
        design = (await client.post(f"/api/product/teacher/courses/{course['id']}/designs",
                                     headers=headers, files={"file": (
                                         "教学设计.txt", "教学目标：掌握 Python 循环和条件分支。通过简单程序练习输入、输出、判断与列表遍历。学生应能解释每一步的变量变化，并通过公开样例检查程序结果。".encode(),
                                     )})).json()
        response = await client.post(f"/api/product/teacher/courses/{course['id']}/generate-questions",
                                     headers=headers, json={"design_id": design["id"], "count": 3})
        assert response.status_code == 503
        assert (await client.get(f"/api/product/teacher/courses/{course['id']}/question-drafts",
                                 headers=headers)).json()["drafts"] == []
    await app.state.business_engine.dispose()
