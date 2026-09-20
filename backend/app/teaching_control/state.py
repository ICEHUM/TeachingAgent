from typing import Literal, TypedDict

TeachingMode = Literal["guided_practice", "teacher_demo", "assessment"]
HelpLevel = Literal["NONE", "L0", "L1", "L2"]
GuidanceKind = Literal["question", "answer", "code_patch"]
ToolCapability = Literal["DIAGNOSTIC", "EVALUATION", "MODIFICATION", "GENERATION", "ADMIN"]
EventType = Literal["check_result", "request_guidance", "run_tool", "submission"]
FailureOrigin = Literal["none", "student", "infrastructure"]
CheckStatus = Literal["not_run", "passed", "student_failure", "infrastructure_failure"]
DecisionKind = Literal[
    "not_decided",
    "execute_tool",
    "generate_guidance",
    "teacher_interrupt",
    "persist_only",
    "reject",
]
AssessmentOutcome = Literal["WAIT", "NEXT", "END"]
FlowStatus = Literal[
    "RUNNING",
    "WAITING_FOR_STUDENT",
    "WAITING_FOR_TEACHER",
    "READY_FOR_NEXT_STAGE",
    "COMPLETED",
    "REJECTED",
]

FAQ_STAGES = [
    "understand_requirements",
    "prepare_sources",
    "implement_retrieval",
    "generate_cited_answer",
    "validate_boundaries",
    "deliver",
]


class TeachingEvent(TypedDict, total=False):
    event_id: str
    event_type: EventType
    attempt_id: str
    task_version: str
    actor_id: str
    actor_role: Literal["student", "teacher", "system"]
    expected_state_version: int
    operation_id: str
    snapshot_id: str
    evidence_refs: list[str]
    evidence_summary: str
    student_observation: str
    check_passed: bool
    failure_origin: FailureOrigin
    requested_guidance_kind: GuidanceKind
    requested_tool: str


class TeachingPolicy(TypedDict):
    failure_threshold: int
    max_help_level: HelpLevel
    require_observation_for_l1: bool
    allow_answer_guidance: bool
    allow_code_patch: bool
    allow_auto_l2: bool
    allowed_tools: list[str]
    tool_capabilities: dict[str, ToolCapability]
    assessment_allowed_capabilities: list[ToolCapability]


class TeachingDecision(TypedDict):
    kind: DecisionKind
    reason: str
    guidance_kind: GuidanceKind | None
    tool_name: str | None


class GuidanceRecord(TypedDict):
    operation_id: str
    level: HelpLevel
    kind: GuidanceKind
    message: str
    evidence_refs: list[str]


class InterventionRecord(TypedDict):
    intervention_id: str
    reason: str
    requested_state_version: int
    persisted: bool


class TeacherResume(TypedDict):
    teacher_id: str
    roles: list[str]
    expected_state_version: int
    response: str
    allow_l2: bool


class PersistenceReceipt(TypedDict):
    operation_id: str
    state_version: int
    duplicate: bool


class StageAssessment(TypedDict):
    passed: bool
    outcome: AssessmentOutcome
    reason: str


class TeachingState(TypedDict):
    # Identifiers and server-owned concurrency values.
    attempt_id: str
    task_version: str
    learner_id: str
    authorized_teacher_ids: list[str]
    state_version: int
    policy_version: str

    # Teaching position and policy. The business database remains authoritative.
    current_stage: str
    stage_index: int
    stage_sequence: list[str]
    mode: TeachingMode
    teacher_policy: TeachingPolicy
    effective_policy: TeachingPolicy

    # One bounded input event and compact evidence references; never raw code or logs.
    incoming_event: TeachingEvent
    event_replayed: bool
    latest_snapshot_id: str | None
    evidence_refs: list[str]
    evidence_summary: str
    student_observation: str | None
    latest_check_status: CheckStatus
    stage_requirements_met: bool
    requirement_result_refs: list[str]
    student_failure_count: int
    infrastructure_failure_count: int

    # Deterministic routing and bounded external-operation references.
    help_level: HelpLevel
    decision: TeachingDecision
    processed_operation_ids: list[str]
    last_tool_result_ref: str | None
    last_tool_status: str | None
    guidance: GuidanceRecord | None
    pending_intervention: InterventionRecord | None
    teacher_resume: TeacherResume | None
    l2_authorized: bool
    persistence: PersistenceReceipt | None

    # A recommendation for workflow movement, never a formal grade.
    stage_assessment: StageAssessment
    flow_status: FlowStatus
    error_code: str | None


DEFAULT_POLICY: TeachingPolicy = {
    "failure_threshold": 3,
    "max_help_level": "L2",
    "require_observation_for_l1": True,
    "allow_answer_guidance": True,
    "allow_code_patch": False,
    "allow_auto_l2": False,
    "allowed_tools": [
        "inspect_workspace",
        "run_student_program",
        "run_faq_tests",
        "validate_retrieval",
        "validate_citations",
        "inspect_runtime_error",
        "run_public_checks",
    ],
    "tool_capabilities": {
        "inspect_workspace": "DIAGNOSTIC",
        "run_student_program": "DIAGNOSTIC",
        "run_faq_tests": "EVALUATION",
        "validate_retrieval": "EVALUATION",
        "validate_citations": "EVALUATION",
        "inspect_runtime_error": "DIAGNOSTIC",
        "run_public_checks": "EVALUATION",
    },
    "assessment_allowed_capabilities": ["DIAGNOSTIC", "EVALUATION"],
}


def new_teaching_state(
    *,
    attempt_id: str,
    task_version: str,
    learner_id: str,
    incoming_event: TeachingEvent,
    authorized_teacher_ids: list[str],
    mode: TeachingMode = "guided_practice",
    state_version: int = 0,
    policy_version: str = "policy-v1",
    teacher_policy: TeachingPolicy | None = None,
    stage_sequence: list[str] | None = None,
) -> TeachingState:
    stages = list(stage_sequence or FAQ_STAGES)
    policy = dict(teacher_policy or DEFAULT_POLICY)
    return {
        "attempt_id": attempt_id,
        "task_version": task_version,
        "learner_id": learner_id,
        "authorized_teacher_ids": list(authorized_teacher_ids),
        "state_version": state_version,
        "policy_version": policy_version,
        "current_stage": stages[0],
        "stage_index": 0,
        "stage_sequence": stages,
        "mode": mode,
        "teacher_policy": policy,
        "effective_policy": dict(policy),
        "incoming_event": dict(incoming_event),
        "event_replayed": False,
        "latest_snapshot_id": None,
        "evidence_refs": [],
        "evidence_summary": "",
        "student_observation": None,
        "latest_check_status": "not_run",
        "stage_requirements_met": False,
        "requirement_result_refs": [],
        "student_failure_count": 0,
        "infrastructure_failure_count": 0,
        "help_level": "NONE",
        "decision": {
            "kind": "not_decided",
            "reason": "No event has been evaluated.",
            "guidance_kind": None,
            "tool_name": None,
        },
        "processed_operation_ids": [],
        "last_tool_result_ref": None,
        "last_tool_status": None,
        "guidance": None,
        "pending_intervention": None,
        "teacher_resume": None,
        "l2_authorized": False,
        "persistence": None,
        "stage_assessment": {
            "passed": False,
            "outcome": "WAIT",
            "reason": "Stage has not been assessed.",
        },
        "flow_status": "RUNNING",
        "error_code": None,
    }
