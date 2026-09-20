from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from .state import GuidanceKind, HelpLevel, TeachingMode, ToolCapability


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """Server-owned limits copied into every auditable execution request."""

    cpu_count: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 128
    max_stdout_bytes: int = 16_384
    max_stderr_bytes: int = 16_384
    network_policy: Literal["internal_only"] = "internal_only"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    uri: str
    kind: Literal["evidence_manifest", "bounded_stdout", "bounded_stderr", "workspace_manifest"]
    sha256: str
    size_bytes: int
    media_type: str = "application/json"


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    attempt_id: str
    task_version: str
    snapshot_id: str
    operation_id: str
    tool_name: str
    status: Literal["succeeded", "student_failure", "infrastructure_failure"]
    code: str
    summary: str
    artifact_refs: tuple[ArtifactRef, ...]
    observed_at: datetime
    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dataclass(frozen=True, slots=True)
class ToolExecutionRequest:
    operation_id: str
    attempt_id: str
    task_version: str
    stage: str
    snapshot_id: str
    tool_name: str
    tool_capability: ToolCapability
    timeout_seconds: int
    resource_policy: ResourcePolicy


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    operation_id: str
    status: Literal["succeeded", "student_failure", "infrastructure_failure"]
    output_ref: str
    summary: str
    evidence: tuple[EvidenceRecord, ...] = field(default_factory=tuple)
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class GuidanceRequest:
    operation_id: str
    attempt_id: str
    task_version: str
    stage: str
    mode: TeachingMode
    level: HelpLevel
    kind: GuidanceKind
    evidence_refs: tuple[str, ...]
    evidence_summary: str


@dataclass(frozen=True, slots=True)
class GuidanceDraft:
    operation_id: str
    level: HelpLevel
    kind: GuidanceKind
    message: str
    evidence_refs: tuple[str, ...]
    next_step: str = ""
    uncertainty: str = ""
    provider: str = ""
    model: str = ""
    request_id: str | None = None
    latency_ms: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    success: bool = True
    fallback_reason: str | None = None
    raw_output_ref: str | None = None
    audit_ref: str | None = None
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class PersistResult:
    operation_id: str
    state_version: int
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class RequirementEvaluation:
    stage_satisfied: bool
    result_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterventionCreation:
    intervention_id: str
    duplicate: bool = False


class OpenHandsExecutorProtocol(Protocol):
    """Bounded execution contract; it cannot select teaching stages or help levels."""

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult: ...


class ToolResultRecorderProtocol(Protocol):
    """Persists validated evidence and snapshot-bound requirement results."""

    def record(self, request: ToolExecutionRequest, result: ToolExecutionResult) -> tuple[str, ...]: ...


class TeachingLLMProtocol(Protocol):
    """Text-generation contract; routing and formal grades stay outside the model."""

    def generate_guidance(self, request: GuidanceRequest) -> GuidanceDraft: ...


class TeachingEventStoreProtocol(Protocol):
    """Business-state compare-and-set boundary used by the stage-01 fake store."""

    def persist(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        expected_state_version: int,
        event: dict[str, object],
    ) -> PersistResult: ...


class RequirementEvaluatorProtocol(Protocol):
    """Server-side requirement aggregation; client pass flags are never accepted."""

    def evaluate(self, *, attempt_id: str, task_version: str, stage: str) -> RequirementEvaluation: ...


class InterventionStoreProtocol(Protocol):
    """Business fact store called before LangGraph interrupt."""

    def create_intervention(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        reason: str,
        requested_state_version: int,
        evidence_refs: tuple[str, ...],
    ) -> InterventionCreation: ...
