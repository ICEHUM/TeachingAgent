from __future__ import annotations

import httpx

from app.agent.teaching_llm import DeepSeekTeachingLLM
from app.business.stage06_api import DemoHealth
from app.teaching_control.protocols import GuidanceRequest


def request(operation: str = "stage06-timeout") -> GuidanceRequest:
    return GuidanceRequest(attempt_id="demo", task_version="FAQ-001-v1", stage="implement_retrieval", mode="guided_practice", level="L0", kind="SCAFFOLD", evidence_refs=("evidence://1",), evidence_summary="检索为空", operation_id=operation)


def test_deepseek_unavailable_is_safe_fallback(tmp_path):
    def handler(_: httpx.Request):
        raise httpx.ConnectError("offline")
    draft = DeepSeekTeachingLLM(transport=httpx.MockTransport(handler), audit_root=tmp_path).generate_guidance(request())
    assert draft.success is False
    assert draft.fallback_reason == "api_unavailable"
    assert draft.level == "L0"
    assert "代码" not in draft.message


def test_health_contract_exposes_statuses_only():
    payload = DemoHealth(business_database="normal", execution_environment="degraded", teaching_flow="normal", model_service="unavailable").model_dump()
    assert set(payload) == {"business_database", "execution_environment", "teaching_flow", "model_service"}
    assert not any(word in str(payload).lower() for word in ("password", "token", "key", "path"))


def test_sse_source_supports_backfill_cursor():
    import inspect

    from app.business import stage05_api
    source = inspect.getsource(stage05_api.event_stream)
    assert "since_state_version" in source
    assert "id: {event.state_version}" in source
