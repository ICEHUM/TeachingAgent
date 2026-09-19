from __future__ import annotations

from dataclasses import dataclass, field, replace

from .errors import StaleStateVersionError
from .protocols import (
    GuidanceDraft,
    GuidanceRequest,
    InterventionCreation,
    PersistResult,
    RequirementEvaluation,
    ToolExecutionRequest,
    ToolExecutionResult,
)


@dataclass
class FakeOpenHandsExecutor:
    """Deterministic fake with an operation ledger; no Docker or OpenHands call occurs."""

    results_by_tool: dict[str, ToolExecutionResult] = field(default_factory=dict)
    calls: list[ToolExecutionRequest] = field(default_factory=list)
    _ledger: dict[str, ToolExecutionResult] = field(default_factory=dict)

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        if request.operation_id in self._ledger:
            return replace(self._ledger[request.operation_id], duplicate=True)
        self.calls.append(request)
        configured = self.results_by_tool.get(request.tool_name)
        result = configured or ToolExecutionResult(
            operation_id=request.operation_id,
            status="succeeded",
            output_ref=f"result://{request.operation_id}",
            summary="Fake public check completed.",
        )
        if result.operation_id != request.operation_id:
            result = replace(result, operation_id=request.operation_id)
        self._ledger[request.operation_id] = result
        return result


@dataclass
class FakeTeachingLLM:
    """Deterministic fake that renders an already-selected help level and kind."""

    calls: list[GuidanceRequest] = field(default_factory=list)
    _ledger: dict[str, GuidanceDraft] = field(default_factory=dict)

    def generate_guidance(self, request: GuidanceRequest) -> GuidanceDraft:
        if request.operation_id in self._ledger:
            return replace(self._ledger[request.operation_id], duplicate=True)
        self.calls.append(request)
        message = {
            "L0": "请先说明你观察到的现象，并指出准备验证的一个条件。",
            "L1": "根据现有证据，先定位匹配条件，再用一个最小输入验证你的判断。",
            "L2": "根据教师允许的范围，参考局部示例后自行完成并重新验证。",
            "NONE": "请补充可核对的运行证据。",
        }[request.level]
        draft = GuidanceDraft(
            operation_id=request.operation_id,
            level=request.level,
            kind=request.kind,
            message=message,
            evidence_refs=request.evidence_refs,
        )
        self._ledger[request.operation_id] = draft
        return draft


@dataclass
class FakeTeachingEventStore:
    """In-memory compare-and-set fake; it is not a classroom persistence solution."""

    versions: dict[str, int] = field(default_factory=dict)
    calls: list[dict[str, object]] = field(default_factory=list)
    _ledger: dict[str, PersistResult] = field(default_factory=dict)

    def persist(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        expected_state_version: int,
        event: dict[str, object],
    ) -> PersistResult:
        if operation_id in self._ledger:
            return replace(self._ledger[operation_id], duplicate=True)
        current = self.versions.setdefault(attempt_id, expected_state_version)
        if current != expected_state_version:
            raise StaleStateVersionError(
                f"Expected state_version {expected_state_version}, current is {current}."
            )
        result = PersistResult(
            operation_id=operation_id,
            state_version=expected_state_version + 1,
        )
        self.versions[attempt_id] = result.state_version
        self.calls.append(
            {
                "operation_id": operation_id,
                "attempt_id": attempt_id,
                "expected_state_version": expected_state_version,
                "event": dict(event),
            }
        )
        self._ledger[operation_id] = result
        return result


@dataclass
class FakeRequirementEvaluator:
    """Server-side fake; tests control results without trusting incoming events."""

    stage_satisfied: bool = False
    result_refs: tuple[str, ...] = ()
    calls: list[dict[str, str]] = field(default_factory=list)

    def evaluate(self, *, attempt_id: str, task_version: str, stage: str) -> RequirementEvaluation:
        self.calls.append({"attempt_id": attempt_id, "task_version": task_version, "stage": stage})
        return RequirementEvaluation(self.stage_satisfied, self.result_refs)


@dataclass
class FakeInterventionStore:
    """Records durable-business ordering before interrupt in stage-02A tests."""

    calls: list[dict[str, object]] = field(default_factory=list)
    _ledger: dict[str, InterventionCreation] = field(default_factory=dict)

    def create_intervention(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        reason: str,
        requested_state_version: int,
        evidence_refs: tuple[str, ...],
    ) -> InterventionCreation:
        if operation_id in self._ledger:
            return replace(self._ledger[operation_id], duplicate=True)
        result = InterventionCreation(intervention_id=f"intervention:{attempt_id}:{operation_id}")
        self.calls.append({
            "operation_id": operation_id,
            "attempt_id": attempt_id,
            "reason": reason,
            "requested_state_version": requested_state_version,
            "evidence_refs": evidence_refs,
        })
        self._ledger[operation_id] = result
        return result
