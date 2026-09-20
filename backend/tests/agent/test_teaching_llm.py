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


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
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
def test_invalid_model_outputs_use_safe_fallback(tmp_path, payload, reason):
    draft = adapter(tmp_path, payload).generate_guidance(request())
    assert draft.success is False
    assert draft.fallback_reason == reason
    assert "？" in draft.message
    assert "```" not in draft.message


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


def test_timeout_uses_safe_fallback_without_blocking(tmp_path):
    draft = adapter(tmp_path, {}, timeout=True).generate_guidance(request())
    assert draft.success is False
    assert draft.fallback_reason == "timeout"


def test_temporary_api_failure_uses_safe_fallback(tmp_path):
    draft = adapter(tmp_path, {}, status=503).generate_guidance(request())
    assert draft.success is False
    assert draft.fallback_reason == "api_unavailable"
