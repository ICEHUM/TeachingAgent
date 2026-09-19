from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .state import GuidanceKind, HelpLevel, TeachingMode


@dataclass(frozen=True, slots=True)
class ToolExecutionRequest:
    operation_id: str
    attempt_id: str
    task_version: str
    stage: str
    snapshot_id: str
    tool_name: str


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    operation_id: str
    status: Literal["succeeded", "student_failure", "infrastructure_failure"]
    output_ref: str
    summary: str
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
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class PersistResult:
    operation_id: str
    state_version: int
    duplicate: bool = False


class OpenHandsExecutorProtocol(Protocol):
    """Bounded execution contract; it cannot select teaching stages or help levels."""

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult: ...


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
