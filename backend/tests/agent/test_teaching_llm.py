from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.agent.llm import ModelSettings
from app.agent.teaching_llm import DeepSeekTeachingLLM
from app.teaching_control.protocols import GuidanceRequest


def settings() -> ModelSettings:
    return ModelSettings(
        llm_model="deepseek-flash",
        llm_api_key=SecretStr("test-only"),
        llm_base_url="https://deepseek.invalid",
        llm_provider="deepseek",
    )


def request(*, mode="guided_practice", level="L0") -> GuidanceRequest:
    return GuidanceRequest(
        operation_id=f"guidance:test-{mode}-{level}",
        attempt_id="attempt-1",
        task_version="FAQ-001-v1",
        stage="implement_retrieval",
        mode=mode,
        level=level,
        kind="question",
        evidence_refs=("artifact://allowed/1",),
        evidence_summary="known_question_hit failed with empty_retrieval",
    )


def response(payload: object, *, status=200):
    def handler(http_request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, request=http_request)
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return httpx.Response(
            200,
            request=http_request,
            headers={"x-request-id": "req-stage04"},
            json={
                "id": "chat-stage04",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 12},
            },
        )

    return httpx.MockTransport(handler)


def adapter(tmp_path, payload: object, *, status=200, timeout=False):
    if timeout:
        def handler(http_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("simulated timeout", request=http_request)

        transport = httpx.MockTransport(handler)
    else:
        transport = response(payload, status=status)
    return DeepSeekTeachingLLM(
        settings=settings(), transport=transport, audit_root=tmp_path
    )


def test_valid_l0_is_accepted_and_audited(tmp_path):
    llm = adapter(
        tmp_path,
        {
            "message": "已知问题当前返回了什么，与未知问题的结果有何不同？",
            "evidence_refs": ["artifact://allowed/1"],
            "next_step": "分别记录两个输入的返回结果。",
            "uncertainty": "low",
        },
    )
    draft = llm.generate_guidance(request())
    assert draft.success is True
    assert draft.request_id == "req-stage04"
    assert draft.prompt_tokens == 20
    assert draft.raw_output_ref and draft.audit_ref
    assert "test-only" not in next(tmp_path.rglob("*.audit.json")).read_text(
        encoding="utf-8"
    )


def test_valid_guidance_with_explanatory_uncertainty_is_kept(tmp_path):
    draft = adapter(tmp_path, {
        "message": "报错指向哪一行？那里的名字与之前赋值时一致吗？",
        "evidence_refs": ["artifact://allowed/1"],
        "next_step": "对照报错行和赋值行，只修改一个名字后重试。",
        "uncertainty": "无法确认学生是否已经查看终端回溯。",
    }).generate_guidance(request())

    assert draft.success is True
    assert draft.fallback_reason is None
    assert draft.uncertainty == "medium"
    assert "报错指向哪一行" in draft.message


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("", "empty_response"),
        ("not-json", "invalid_structure"),
        ({
            "message": "你观察到了什么？",
            "evidence_refs": ["artifact://not-allowed"],
            "next_step": "记录结果。",
            "uncertainty": "medium",
        }, "invalid_evidence_ref"),
        ({
            "message": "你观察到了什么？",
            "evidence_refs": [],
            "next_step": "记录结果。",
            "uncertainty": "medium",
            "help_level": "L2",
        }, "help_level_violation"),
    ],
)
def test_invalid_model_outputs_are_not_published_as_guidance(tmp_path, payload, reason):
    draft = adapter(tmp_path, payload).generate_guidance(request())
    assert draft.success is False
    assert draft.fallback_reason == reason
    assert draft.message == ""
    assert draft.next_step == ""
    assert draft.evidence_refs == ()
    audit = json.loads(next(tmp_path.rglob("*.audit.json")).read_text(encoding="utf-8"))
    assert audit["final"]["message"] == ""


def test_assessment_solution_content_is_rejected(tmp_path):
    draft = adapter(
        tmp_path,
        {
            "message": "请复制以下完整代码。",
            "evidence_refs": ["artifact://allowed/1"],
            "next_step": "直接替换现有实现。",
            "uncertainty": "low",
        },
    ).generate_guidance(request(mode="assessment", level="L0"))
    assert draft.success is False
    assert draft.fallback_reason == "assessment_policy_violation"
    assert "代码" not in draft.message


def test_timeout_is_a_failed_generation_without_answer(tmp_path):
    draft = adapter(tmp_path, {}, timeout=True).generate_guidance(request())
    assert draft.success is False
    assert draft.fallback_reason == "timeout"
    assert draft.message == ""


def test_temporary_api_failure_is_not_published_as_answer(tmp_path):
    draft = adapter(tmp_path, {}, status=503).generate_guidance(request())
    assert draft.success is False
    assert draft.fallback_reason == "api_unavailable"
    assert draft.message == ""


def test_policy_scenario_prompt_is_grounded_in_real_workspace_contract(tmp_path):
    policy_request = GuidanceRequest(
        operation_id="guidance:policy-context",
        attempt_id="attempt-policy",
        task_version="POLICY-FAQ-001-v1",
        stage="implement_retrieval",
        mode="guided_practice",
        level="L0",
        kind="question",
        evidence_refs=("artifact://allowed/1",),
        evidence_summary="known_question_hit failed with empty_retrieval",
    )
    llm = adapter(tmp_path, {})
    messages = llm._prompt(policy_request)
    context = json.loads(messages[1]["content"])

    assert (
        context["server_owned_task_context"]["task"]
        == "职业学校学生实习政策问答助手"
    )
    assert "没有向量索引" in context["server_owned_task_context"]["workspace"]


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("L1", "宽泛关键词"),
        ("L2", "通用词集合"),
    ],
)
def test_policy_practice_model_failure_has_no_template(tmp_path, level, expected):
    policy_request = GuidanceRequest(
        operation_id=f"guidance:policy-scaffold-{level}",
        attempt_id="attempt-policy",
        task_version="POLICY-FAQ-001-v1",
        stage="implement_retrieval",
        mode="guided_practice",
        level=level,
        kind="question",
        evidence_refs=("artifact://allowed/1",),
        evidence_summary="unknown_question_no_fabrication failed",
    )

    draft = adapter(tmp_path, {}, timeout=True).generate_guidance(policy_request)

    assert draft.success is False
    assert draft.fallback_reason == "timeout"
    assert expected not in draft.message
    assert draft.message == ""
    assert draft.next_step == ""


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("L0", "新增 answer"),
        ("L1", "citations[0]"),
        ("L2", "hits = retrieve"),
    ],
)
def test_cited_answer_stage_model_failure_has_no_template(tmp_path, level, expected):
    policy_request = GuidanceRequest(
        operation_id=f"guidance:answer-scaffold-{level}",
        attempt_id="attempt-policy",
        task_version="POLICY-FAQ-001-v1",
        stage="generate_cited_answer",
        mode="guided_practice",
        level=level,
        kind="question",
        evidence_refs=("artifact://allowed/1",),
        evidence_summary="answer_function_present failed",
    )

    draft = adapter(tmp_path, {}, timeout=True).generate_guidance(policy_request)

    assert draft.success is False
    assert expected not in draft.message
    assert draft.message == ""


@pytest.mark.parametrize(
    ("level", "expected"),
    [("L0", "变量名未定义"), ("L1", "报错行参与相加的变量")],
)
def test_python_name_error_model_failure_has_no_template(tmp_path, level, expected):
    python_request = GuidanceRequest(
        operation_id=f"guidance:python-name-error-{level}",
        attempt_id="attempt-python",
        task_version="PYB-01-v1",
        stage="write_program",
        mode="guided_practice",
        level=level,
        kind="question",
        evidence_refs=("artifact://allowed/1",),
        evidence_summary=(
            "run_python_public_tests failed: check_failed. "
            "observed_public_diagnosis=undefined_name."
        ),
    )

    draft = adapter(tmp_path, {}, timeout=True).generate_guidance(python_request)

    assert draft.fallback_reason == "timeout"
    assert expected not in draft.message
    assert draft.message == ""
    assert draft.next_step == ""


def test_python_model_failure_does_not_guess_cause(tmp_path):
    python_request = GuidanceRequest(
        operation_id="guidance:python-no-diagnosis",
        attempt_id="attempt-python",
        task_version="PYB-01-v1",
        stage="write_program",
        mode="guided_practice",
        level="L1",
        kind="question",
        evidence_refs=(),
        evidence_summary="addition_public_tests=NOT_SATISFIED",
    )

    draft = adapter(tmp_path, {}, timeout=True).generate_guidance(python_request)

    assert draft.success is False
    assert draft.message == ""


def test_duplicate_legacy_failed_audit_does_not_resurface_template(tmp_path):
    llm = adapter(tmp_path, {}, timeout=True)
    original = request()
    failed = llm.generate_guidance(original)
    assert failed.success is False
    audit_path = next(tmp_path.rglob("*.audit.json"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["final"]["message"] = "旧版本预写的指导文本"
    audit["final"]["next_step"] = "旧版本预写的下一步"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    duplicate = llm.generate_guidance(original)
    assert duplicate.duplicate is True
    assert duplicate.success is False
    assert duplicate.message == ""
    assert duplicate.next_step == ""
