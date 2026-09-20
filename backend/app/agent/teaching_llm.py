"""DeepSeek adapter that only renders server-selected teaching guidance."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.teaching_control.protocols import GuidanceDraft, GuidanceRequest

from .llm import ModelSettings

ROOT = Path(__file__).resolve().parents[3]
MODEL_RUN_ROOT = ROOT / "workspaces" / "model-runs"
CONTROL_FIELDS = {
    "help_level",
    "teaching_mode",
    "stage",
    "route",
    "formal_grade",
    "tool_capability",
    "code_patch_permission",
}
CODE_PATTERN = re.compile(
    r"```|diff --git|apply_patch|(?:^|\n)\s*(?:def|class|import|from|return|if|for|while)\b",
    re.IGNORECASE,
)
ANSWER_MARKERS = (
    "完整答案",
    "完整代码",
    "直接替换",
    "复制以下",
    "代码补丁",
    "最终实现",
    "修改为：",
)


class GuidanceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=800)
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)
    next_step: str = Field(min_length=1, max_length=300)
    uncertainty: Literal["low", "medium", "high"]


class GuidancePolicyError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fallback(request: GuidanceRequest) -> tuple[str, str, str]:
    if request.mode == "assessment":
        return (
            "请先记录测试呈现的现象，并说明你准备核对的一个条件。",
            "使用现有证据完成一次最小验证并记录结果。",
            "medium",
        )
    if request.level == "L0":
        return (
            "请先描述已知问题输入时出现了什么现象，以及正常加载资料后哪一项结果仍不符合预期？",
            "用一个已知问题和一个未知问题分别记录当前返回结果。",
            "medium",
        )
    if request.level == "L1":
        return (
            "现有证据指向检索匹配环节。请检查查询文本与资料问题字段的比较条件，再自行验证判断。",
            "修改前先写下预期匹配条件，然后重新运行已知问题和未知问题测试。",
            "medium",
        )
    return (
        "教师已允许局部示例级指导。请围绕检索函数中的匹配条件做最小范围调整，并保持未知问题返回空结果。",
        "自行完成修改后，同时运行已知问题、未知问题和引用检查。",
        "medium",
    )


def _validate_output(request: GuidanceRequest, payload: dict[str, Any]) -> GuidanceOutput:
    attempted_controls = CONTROL_FIELDS.intersection(payload)
    if attempted_controls:
        raise GuidancePolicyError("help_level_violation")
    try:
        output = GuidanceOutput.model_validate(payload)
    except ValidationError as exc:
        raise GuidancePolicyError("invalid_structure") from exc
    allowed = set(request.evidence_refs)
    if any(reference not in allowed for reference in output.evidence_refs):
        raise GuidancePolicyError("invalid_evidence_ref")
    combined = f"{output.message}\n{output.next_step}"
    if CODE_PATTERN.search(combined) or any(marker in combined for marker in ANSWER_MARKERS):
        reason = "assessment_policy_violation" if request.mode == "assessment" else "forbidden_answer"
        raise GuidancePolicyError(reason)
    if request.level == "L0" and not ({"?", "？"} & set(output.message)):
        raise GuidancePolicyError("help_level_violation")
    if request.mode == "assessment" and request.level != "L0":
        raise GuidancePolicyError("help_level_violation")
    return output


class DeepSeekTeachingLLM:
    """Calls DeepSeek for text only, validates it, and always returns a safe draft."""

    def __init__(
        self,
        *,
        settings: ModelSettings | None = None,
        timeout_seconds: float = 20,
        transport: httpx.BaseTransport | None = None,
        audit_root: Path = MODEL_RUN_ROOT,
    ) -> None:
        self.settings = settings or ModelSettings()
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.audit_root = audit_root.resolve()

    def _paths(self, request: GuidanceRequest) -> tuple[Path, Path]:
        attempt = hashlib.sha256(request.attempt_id.encode()).hexdigest()[:24]
        operation = hashlib.sha256(request.operation_id.encode()).hexdigest()
        directory = self.audit_root / f"attempt-{attempt}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{operation}.raw.json", directory / f"{operation}.audit.json"

    def _load_duplicate(self, request: GuidanceRequest, audit_path: Path) -> GuidanceDraft:
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        final = payload["final"]
        return GuidanceDraft(
            operation_id=request.operation_id,
            level=request.level,
            kind=request.kind,
            message=final["message"],
            evidence_refs=tuple(final["evidence_refs"]),
            next_step=final["next_step"],
            uncertainty=final["uncertainty"],
            provider=payload["provider"],
            model=payload["model"],
            request_id=payload.get("request_id"),
            latency_ms=payload["latency_ms"],
            prompt_tokens=payload.get("prompt_tokens"),
            completion_tokens=payload.get("completion_tokens"),
            success=payload["success"],
            fallback_reason=payload.get("fallback_reason"),
            raw_output_ref=payload.get("raw_output_ref"),
            audit_ref=payload["audit_ref"],
            duplicate=True,
        )

    def _prompt(self, request: GuidanceRequest) -> list[dict[str, str]]:
        system = (
            "你是FAQ实训教学指导文本生成器。教学模式、帮助级别和路由已由服务端决定，"
            "你无权修改。只输出JSON对象，且只能含message、evidence_refs、next_step、"
            "uncertainty四个字段。uncertainty只能是low、medium、high。不得输出思维链、"
            "完整答案、代码块、代码补丁或可直接复制的修复代码。L0只能提问并引导学生描述"
            "现象和缩小范围；L1可指出概念、模块或检查方向，但不能给出完整修复；assessment"
            "只能使用L0，不得透露解法。evidence_refs只能从允许集合中选择。message控制在"
            "180个汉字以内，next_step控制在80个汉字以内。"
        )
        context = {
            "task_version": request.task_version,
            "stage": request.stage,
            "mode": request.mode,
            "server_selected_level": request.level,
            "server_selected_guidance_type": request.kind,
            "allowed_evidence_refs": list(request.evidence_refs),
            "bounded_evidence_summary": request.evidence_summary,
        }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

    def generate_guidance(self, request: GuidanceRequest) -> GuidanceDraft:
        raw_path, audit_path = self._paths(request)
        if audit_path.exists():
            return self._load_duplicate(request, audit_path)

        provider = self.settings.llm_provider
        model = self.settings.llm_model
        started = time.perf_counter()
        request_id: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        raw_content = ""
        success = False
        fallback_reason: str | None = None
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = client.post(
                    self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                    headers={
                        "Authorization": (
                            "Bearer " + self.settings.llm_api_key.get_secret_value()
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": self._prompt(request),
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                        "max_tokens": 1000,
                    },
                )
                response.raise_for_status()
                body = response.json()
                request_id = response.headers.get("x-request-id") or body.get("id")
                usage = body.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
                raw_content = body["choices"][0]["message"]["content"]
                parsed = json.loads(raw_content)
                if not isinstance(parsed, dict):
                    raise GuidancePolicyError("invalid_structure")
                output = _validate_output(request, parsed)
                success = True
        except httpx.TimeoutException:
            fallback_reason = "timeout"
        except httpx.HTTPStatusError as exc:
            fallback_reason = (
                "api_unavailable" if exc.response.status_code >= 500 else "api_error"
            )
        except httpx.HTTPError:
            fallback_reason = "api_unavailable"
        except (KeyError, IndexError, TypeError):
            fallback_reason = "invalid_structure"
        except json.JSONDecodeError:
            fallback_reason = "invalid_structure"
        except GuidancePolicyError as exc:
            fallback_reason = exc.reason

        if not success:
            message, next_step, uncertainty = _fallback(request)
            output = GuidanceOutput(
                message=message,
                evidence_refs=list(request.evidence_refs),
                next_step=next_step,
                uncertainty=uncertainty,
            )

        latency_ms = round((time.perf_counter() - started) * 1000)
        operation = hashlib.sha256(request.operation_id.encode()).hexdigest()
        attempt = hashlib.sha256(request.attempt_id.encode()).hexdigest()[:24]
        raw_ref = f"model-raw://{attempt}/{operation}"
        audit_ref = f"model-audit://{attempt}/{operation}"
        raw_payload = {
            "provider": provider,
            "model": model,
            "request_id": request_id,
            "content": raw_content,
        }
        audit_payload = {
            "provider": provider,
            "model": model,
            "request_id": request_id,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "success": success,
            "fallback_reason": fallback_reason,
            "guidance_level": request.level,
            "guidance_kind": request.kind,
            "mode": request.mode,
            "evidence_refs": list(output.evidence_refs),
            "raw_output_ref": raw_ref,
            "audit_ref": audit_ref,
            "created_at": datetime.now(UTC).isoformat(),
            "final": output.model_dump(),
        }
        try:
            raw_path.write_text(
                json.dumps(raw_payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            audit_path.write_text(
                json.dumps(audit_payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            raw_ref = None
            audit_ref = None

        return GuidanceDraft(
            operation_id=request.operation_id,
            level=request.level,
            kind=request.kind,
            message=output.message,
            evidence_refs=tuple(output.evidence_refs),
            next_step=output.next_step,
            uncertainty=output.uncertainty,
            provider=provider,
            model=model,
            request_id=request_id,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            success=success,
            fallback_reason=fallback_reason,
            raw_output_ref=raw_ref,
            audit_ref=audit_ref,
        )
