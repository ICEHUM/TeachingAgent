from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .errors import (
    AuthorizationError,
    InvalidContextError,
    PolicyViolationError,
    StageAdvanceError,
    StaleStateVersionError,
)
from .protocols import (
    GuidanceRequest,
    InterventionStoreProtocol,
    OpenHandsExecutorProtocol,
    RequirementEvaluatorProtocol,
    TeachingEventStoreProtocol,
    TeachingLLMProtocol,
    ToolExecutionRequest,
)
from .state import (
    AssessmentOutcome,
    GuidanceKind,
    HelpLevel,
    TeacherResume,
    TeachingDecision,
    TeachingPolicy,
    TeachingState,
)

MAX_EVIDENCE_REFS = 16
MAX_EVIDENCE_SUMMARY = 512
MAX_STUDENT_OBSERVATION = 512
MAX_OPERATION_IDS = 32


@dataclass(frozen=True, slots=True)
class TeachingGraphDependencies:
    executor: OpenHandsExecutorProtocol
    llm: TeachingLLMProtocol
    event_store: TeachingEventStoreProtocol
    requirement_evaluator: RequirementEvaluatorProtocol
    intervention_store: InterventionStoreProtocol


def _bounded_operations(existing: list[str], operation_id: str) -> list[str]:
    if operation_id in existing:
        return list(existing)
    return [*existing, operation_id][-MAX_OPERATION_IDS:]


def _required_text(event: Mapping[str, object], key: str) -> str:
    value = event.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidContextError(f"Event field {key!r} is required.")
    return value.strip()


def _valid_observation(value: str | None) -> bool:
    return bool(value and len(value.strip()) >= 8)


def _help_rank(level: HelpLevel) -> int:
    return {"NONE": -1, "L0": 0, "L1": 1, "L2": 2}[level]


def _cap_help(level: HelpLevel, maximum: HelpLevel) -> HelpLevel:
    return level if _help_rank(level) <= _help_rank(maximum) else maximum


def receive_event(state: TeachingState) -> dict[str, object]:
    event = state["incoming_event"]
    for key in (
        "event_id",
        "event_type",
        "attempt_id",
        "task_version",
        "actor_id",
        "actor_role",
        "operation_id",
    ):
        _required_text(event, key)
    if not isinstance(event.get("expected_state_version"), int):
        raise InvalidContextError("Event expected_state_version must be an integer.")
    summary = str(event.get("evidence_summary", "")).strip()
    observation = str(event.get("student_observation", "")).strip()
    refs = event.get("evidence_refs", [])
    if not isinstance(refs, list) or not all(isinstance(item, str) for item in refs):
        raise InvalidContextError("Event evidence_refs must be a list of references.")
    if len(refs) > MAX_EVIDENCE_REFS:
        raise InvalidContextError("Event contains too many evidence references.")
    if len(summary) > MAX_EVIDENCE_SUMMARY or len(observation) > MAX_STUDENT_OBSERVATION:
        raise InvalidContextError("Event evidence must contain references and bounded summaries.")
    persist_operation = f"persist:{event['operation_id']}"
    return {
        "event_replayed": persist_operation in state["processed_operation_ids"],
        "decision": {
            "kind": "not_decided",
            "reason": "Event received and awaiting deterministic evaluation.",
            "guidance_kind": None,
            "tool_name": None,
        },
        "guidance": None,
        "teacher_resume": None,
        "persistence": None,
        "last_tool_result_ref": None,
        "last_tool_status": None,
        "stage_assessment": {
            "passed": False,
            "outcome": "WAIT",
            "reason": "Current event has not been assessed.",
        },
        "flow_status": "RUNNING",
        "error_code": None,
    }


def validate_context(state: TeachingState) -> dict[str, object]:
    event = state["incoming_event"]
    if event["attempt_id"] != state["attempt_id"]:
        raise InvalidContextError("Event attempt_id does not match the active attempt.")
    if event["task_version"] != state["task_version"]:
        raise InvalidContextError("Event task_version does not match the active task version.")
    if event["expected_state_version"] != state["state_version"]:
        raise StaleStateVersionError(
            f"Expected state_version {event['expected_state_version']}, "
            f"current is {state['state_version']}."
        )
    if event["actor_role"] == "student" and event["actor_id"] != state["learner_id"]:
        raise AuthorizationError("Student cannot act on another learner's attempt.")
    if event["actor_role"] not in {"student", "system"}:
        raise AuthorizationError("Teacher continuation must use the interrupt resume contract.")
    return {}


def load_policy(state: TeachingState) -> dict[str, object]:
    configured = dict(state["teacher_policy"])
    threshold = configured.get("failure_threshold")
    if not isinstance(threshold, int) or threshold < 1:
        raise InvalidContextError("failure_threshold must be a positive integer.")
    if configured.get("max_help_level") not in {"L0", "L1", "L2"}:
        raise InvalidContextError("max_help_level must be L0, L1, or L2.")
    effective = cast(TeachingPolicy, configured)
    if state["mode"] == "assessment":
        effective = {
            **effective,
            "max_help_level": "L0",
            "allow_answer_guidance": False,
            "allow_code_patch": False,
            "allow_auto_l2": False,
        }
    return {"effective_policy": effective}


def collect_evidence(state: TeachingState) -> dict[str, object]:
    if state["event_replayed"]:
        return {}
    event = state["incoming_event"]
    refs = list(event.get("evidence_refs", []))[-MAX_EVIDENCE_REFS:]
    summary = str(event.get("evidence_summary", "")).strip()
    observation = str(event.get("student_observation", "")).strip() or None
    update: dict[str, object] = {
        "latest_snapshot_id": event.get("snapshot_id") or state["latest_snapshot_id"],
        "evidence_refs": refs,
        "evidence_summary": summary,
        "student_observation": observation,
    }
    if event["event_type"] != "check_result":
        return update
    if event.get("failure_origin", "none") == "infrastructure":
        update.update(
            latest_check_status="infrastructure_failure",
            infrastructure_failure_count=state["infrastructure_failure_count"] + 1,
        )
    elif bool(event.get("check_passed", False)):
        update["latest_check_status"] = "passed"
    else:
        update.update(
            latest_check_status="student_failure",
            student_failure_count=state["student_failure_count"] + 1,
        )
    return update


def _select_help_level(state: TeachingState) -> HelpLevel:
    failures = state["student_failure_count"]
    observation_valid = _valid_observation(state["student_observation"])
    if failures >= 3 and observation_valid:
        selected: HelpLevel = "L2"
    elif failures >= 2 and observation_valid:
        selected = "L1"
    else:
        selected = "L0"
    return _cap_help(selected, state["effective_policy"]["max_help_level"])


def decide_action(state: TeachingState) -> dict[str, object]:
    event = state["incoming_event"]
    policy = state["effective_policy"]
    stage = state["current_stage"]
    if state["event_replayed"]:
        decision: TeachingDecision = {
            "kind": "persist_only",
            "reason": f"Duplicate operation ignored at stage {stage}.",
            "guidance_kind": None,
            "tool_name": None,
        }
        return {"decision": decision}

    requested_kind = cast(GuidanceKind, event.get("requested_guidance_kind", "question"))
    requested_tool = str(event.get("requested_tool", "")) or None
    if requested_kind == "answer" and not policy["allow_answer_guidance"]:
        return {
            "decision": {
                "kind": "reject",
                "reason": "Answer-style guidance is disabled by the effective teaching policy.",
                "guidance_kind": requested_kind,
                "tool_name": None,
            },
            "error_code": "ANSWER_GUIDANCE_FORBIDDEN",
        }
    if requested_kind == "code_patch" and not policy["allow_code_patch"]:
        return {
            "decision": {
                "kind": "reject",
                "reason": "Code patches are disabled by the effective teaching policy.",
                "guidance_kind": requested_kind,
                "tool_name": requested_tool,
            },
            "error_code": "CODE_PATCH_FORBIDDEN",
        }
    if requested_tool == "apply_patch" and not policy["allow_code_patch"]:
        return {
            "decision": {
                "kind": "reject",
                "reason": "The apply_patch tool is disabled by the effective teaching policy.",
                "guidance_kind": None,
                "tool_name": requested_tool,
            },
            "error_code": "CODE_PATCH_FORBIDDEN",
        }
    if event["event_type"] == "run_tool" and requested_tool:
        capability = policy["tool_capabilities"].get(requested_tool)
        if capability is None or requested_tool not in policy["allowed_tools"]:
            return {
                "decision": {
                    "kind": "reject",
                    "reason": "Requested tool is not allowed.",
                    "guidance_kind": None,
                    "tool_name": requested_tool,
                },
                "error_code": "TOOL_FORBIDDEN",
            }
        if (
            state["mode"] == "assessment"
            and capability not in policy["assessment_allowed_capabilities"]
        ):
            return {
                "decision": {
                    "kind": "reject",
                    "reason": f"Tool capability {capability} is forbidden in assessment mode.",
                    "guidance_kind": None,
                    "tool_name": requested_tool,
                },
                "error_code": "TOOL_CAPABILITY_FORBIDDEN",
            }
    if state["latest_check_status"] == "infrastructure_failure":
        return {
            "decision": {
                "kind": "persist_only",
                "reason": f"Infrastructure failure recorded separately at stage {stage}.",
                "guidance_kind": None,
                "tool_name": None,
            }
        }
    if (
        state["latest_check_status"] == "student_failure"
        and state["student_failure_count"] >= policy["failure_threshold"]
    ):
        operation_id = event["operation_id"]
        intervention_id = (
            f"intervention:{state['attempt_id']}:{state['state_version']}:{operation_id}"
        )
        return {
            "decision": {
                "kind": "teacher_interrupt",
                "reason": (
                    f"Student failure threshold {policy['failure_threshold']} reached at "
                    f"stage {stage}; teacher review required."
                ),
                "guidance_kind": None,
                "tool_name": None,
            },
            "pending_intervention": {
                "intervention_id": intervention_id,
                "reason": "failure_threshold_reached",
                "requested_state_version": state["state_version"],
                "persisted": False,
            },
            "flow_status": "WAITING_FOR_TEACHER",
        }
    if state["latest_check_status"] == "student_failure":
        level = _select_help_level(state)
        if level == "L2" and not (policy["allow_auto_l2"] or state["l2_authorized"]):
            operation_id = event["operation_id"]
            return {
                "decision": {
                    "kind": "teacher_interrupt",
                    "reason": "L2 requires explicit task policy or teacher authorization.",
                    "guidance_kind": None,
                    "tool_name": None,
                },
                "pending_intervention": {
                    "intervention_id": f"pending:{operation_id}",
                    "reason": "l2_authorization_required",
                    "requested_state_version": state["state_version"],
                    "persisted": False,
                },
                "flow_status": "WAITING_FOR_TEACHER",
            }
        return {
            "help_level": level,
            "decision": {
                "kind": "generate_guidance",
                "reason": (
                    f"Hard rules selected {level} from mode={state['mode']}, "
                    f"policy={state['policy_version']}, stage={stage}, "
                    f"failures={state['student_failure_count']}, "
                    f"observation_valid={_valid_observation(state['student_observation'])}."
                ),
                "guidance_kind": "question",
                "tool_name": None,
            },
        }
    if event["event_type"] == "request_guidance":
        level = _cap_help("L0", policy["max_help_level"])
        return {
            "help_level": level,
            "decision": {
                "kind": "generate_guidance",
                "reason": f"Safe guidance request accepted at stage {stage}.",
                "guidance_kind": requested_kind,
                "tool_name": None,
            },
        }
    if event["event_type"] == "run_tool":
        if not requested_tool:
            return {
                "decision": {
                    "kind": "reject",
                    "reason": "Requested tool is not allowed by the effective teaching policy.",
                    "guidance_kind": None,
                    "tool_name": requested_tool,
                },
                "error_code": "TOOL_FORBIDDEN",
            }
        return {
            "decision": {
                "kind": "execute_tool",
                "reason": f"Allowed deterministic tool requested at stage {stage}.",
                "guidance_kind": None,
                "tool_name": requested_tool,
            }
        }
    return {
        "decision": {
            "kind": "persist_only",
            "reason": f"No external action required at stage {stage}.",
            "guidance_kind": None,
            "tool_name": None,
        }
    }


def route_decision(state: TeachingState) -> str:
    return {
        "execute_tool": "execute_tool",
        "generate_guidance": "generate_guidance",
        "teacher_interrupt": "persist_intervention",
        "persist_only": "persist_event",
        "reject": "persist_event",
    }[state["decision"]["kind"]]


def _persist_intervention(
    state: TeachingState, dependencies: TeachingGraphDependencies
) -> dict[str, object]:
    pending = state["pending_intervention"]
    if pending is None:
        raise InvalidContextError("Intervention persistence requires a pending intervention.")
    result = dependencies.intervention_store.create_intervention(
        operation_id=f"intervention:{state['incoming_event']['operation_id']}",
        attempt_id=state["attempt_id"],
        reason=pending["reason"],
        requested_state_version=state["state_version"],
        evidence_refs=tuple(state["evidence_refs"]),
    )
    return {
        "pending_intervention": {
            **pending,
            "intervention_id": result.intervention_id,
            "persisted": True,
        }
    }


def _execute_tool(
    state: TeachingState, dependencies: TeachingGraphDependencies
) -> dict[str, object]:
    event = state["incoming_event"]
    operation_id = f"tool:{event['operation_id']}"
    snapshot_id = state["latest_snapshot_id"]
    if not snapshot_id:
        raise InvalidContextError("Tool execution requires a snapshot_id reference.")
    result = dependencies.executor.execute(
        ToolExecutionRequest(
            operation_id=operation_id,
            attempt_id=state["attempt_id"],
            task_version=state["task_version"],
            stage=state["current_stage"],
            snapshot_id=snapshot_id,
            tool_name=cast(str, state["decision"]["tool_name"]),
        )
    )
    update: dict[str, object] = {
        "processed_operation_ids": _bounded_operations(
            state["processed_operation_ids"], operation_id
        ),
        "last_tool_result_ref": result.output_ref,
        "last_tool_status": result.status,
    }
    if result.status == "infrastructure_failure":
        update.update(
            latest_check_status="infrastructure_failure",
            infrastructure_failure_count=state["infrastructure_failure_count"] + 1,
        )
    elif result.status == "student_failure":
        update.update(
            latest_check_status="student_failure",
            student_failure_count=state["student_failure_count"] + 1,
        )
    return update


def _generate_guidance(
    state: TeachingState, dependencies: TeachingGraphDependencies
) -> dict[str, object]:
    event = state["incoming_event"]
    kind = cast(GuidanceKind, state["decision"]["guidance_kind"])
    policy = state["effective_policy"]
    if kind == "answer" and not policy["allow_answer_guidance"]:
        raise PolicyViolationError("Guidance adapter received forbidden answer request.")
    if kind == "code_patch" and not policy["allow_code_patch"]:
        raise PolicyViolationError("Guidance adapter received forbidden code patch request.")
    operation_id = f"guidance:{event['operation_id']}"
    draft = dependencies.llm.generate_guidance(
        GuidanceRequest(
            operation_id=operation_id,
            attempt_id=state["attempt_id"],
            task_version=state["task_version"],
            stage=state["current_stage"],
            mode=state["mode"],
            level=state["help_level"],
            kind=kind,
            evidence_refs=tuple(state["evidence_refs"]),
            evidence_summary=state["evidence_summary"],
        )
    )
    if draft.level != state["help_level"] or draft.kind != kind:
        raise PolicyViolationError("Guidance adapter changed the server-selected policy.")
    return {
        "processed_operation_ids": _bounded_operations(
            state["processed_operation_ids"], operation_id
        ),
        "guidance": {
            "operation_id": operation_id,
            "level": draft.level,
            "kind": draft.kind,
            "message": draft.message,
            "evidence_refs": list(draft.evidence_refs),
        },
    }


def teacher_interrupt(state: TeachingState) -> dict[str, object]:
    pending = state["pending_intervention"]
    if pending is None:
        raise InvalidContextError("Teacher interrupt requires a pending intervention.")
    if not pending["persisted"]:
        raise InvalidContextError("Intervention must be persisted before interrupt.")
    resumed = interrupt(
        {
            "attempt_id": state["attempt_id"],
            "intervention_id": pending["intervention_id"],
            "state_version": state["state_version"],
            "stage": state["current_stage"],
            "evidence_refs": list(state["evidence_refs"]),
            "reason": pending["reason"],
        }
    )
    if not isinstance(resumed, dict):
        raise InvalidContextError("Teacher resume payload must be an object.")
    teacher_id = str(resumed.get("teacher_id", ""))
    roles = resumed.get("roles", [])
    expected_version = resumed.get("expected_state_version")
    if not isinstance(roles, list) or "teacher" not in roles:
        raise AuthorizationError("Teacher role is required to resume an intervention.")
    if teacher_id not in state["authorized_teacher_ids"]:
        raise AuthorizationError("Teacher is not assigned to this attempt.")
    if expected_version != state["state_version"]:
        raise StaleStateVersionError(
            f"Resume expected state_version {expected_version}, "
            f"current is {state['state_version']}."
        )
    teacher_resume: TeacherResume = {
        "teacher_id": teacher_id,
        "roles": list(roles),
        "expected_state_version": cast(int, expected_version),
        "response": str(resumed.get("response", "")).strip(),
        "allow_l2": bool(resumed.get("allow_l2", False)),
    }
    return {
        "teacher_resume": teacher_resume,
        "pending_intervention": None,
        "flow_status": "RUNNING",
        "l2_authorized": bool(resumed.get("allow_l2", False)),
    }


def _persist_event(
    state: TeachingState, dependencies: TeachingGraphDependencies
) -> dict[str, object]:
    event = state["incoming_event"]
    operation_id = f"persist:{event['operation_id']}"
    result = dependencies.event_store.persist(
        operation_id=operation_id,
        attempt_id=state["attempt_id"],
        expected_state_version=state["state_version"],
        event={
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "decision": state["decision"]["kind"],
            "stage": state["current_stage"],
            "snapshot_id": state["latest_snapshot_id"],
            "evidence_refs": list(state["evidence_refs"]),
            "check_status": state["latest_check_status"],
            "help_level": state["help_level"],
        },
    )
    return {
        "state_version": result.state_version,
        "processed_operation_ids": _bounded_operations(
            state["processed_operation_ids"], operation_id
        ),
        "persistence": {
            "operation_id": operation_id,
            "state_version": result.state_version,
            "duplicate": result.duplicate,
        },
    }


def stage_assessment(state: TeachingState) -> dict[str, object]:
    if state["event_replayed"]:
        passed = False
        outcome: AssessmentOutcome = "WAIT"
        reason = "Duplicate operation cannot advance a stage."
    elif state["decision"]["kind"] == "reject":
        passed = False
        outcome = "WAIT"
        reason = "Rejected action cannot advance a stage."
    elif state["latest_check_status"] == "infrastructure_failure":
        passed = False
        outcome = "WAIT"
        reason = "Infrastructure failure is unverified, not a student stage failure."
    else:
        passed = (
            state["latest_check_status"] == "passed" and state["stage_requirements_met"]
        )
        if not passed:
            outcome = "WAIT"
            reason = "Both a passing check and stage requirements are required."
        elif state["stage_index"] == len(state["stage_sequence"]) - 1:
            outcome = "END"
            reason = "Final stage requirements passed; teacher grading remains external."
        else:
            outcome = "NEXT"
            reason = "Current stage requirements passed."
    return {
        "stage_assessment": {
            "passed": passed,
            "outcome": outcome,
            "reason": reason,
        }
    }


def _evaluate_requirements(
    state: TeachingState, dependencies: TeachingGraphDependencies
) -> dict[str, object]:
    evaluation = dependencies.requirement_evaluator.evaluate(
        attempt_id=state["attempt_id"],
        task_version=state["task_version"],
        stage=state["current_stage"],
    )
    return {
        "stage_requirements_met": evaluation.stage_satisfied,
        "requirement_result_refs": list(evaluation.result_refs),
    }


def route_assessment(state: TeachingState) -> str:
    return state["stage_assessment"]["outcome"].lower()


def wait_state(state: TeachingState) -> dict[str, object]:
    status = "REJECTED" if state["decision"]["kind"] == "reject" else "WAITING_FOR_STUDENT"
    return {"flow_status": status}


def _advance_stage(
    state: TeachingState, dependencies: TeachingGraphDependencies
) -> dict[str, object]:
    if not state["stage_assessment"]["passed"]:
        raise StageAdvanceError("Cannot advance without a passing stage assessment.")
    next_index = state["stage_index"] + 1
    next_stage = state["stage_sequence"][next_index]
    event_operation = state["incoming_event"]["operation_id"]
    operation_id = f"advance:{event_operation}:{next_index}"
    result = dependencies.event_store.persist(
        operation_id=operation_id,
        attempt_id=state["attempt_id"],
        expected_state_version=state["state_version"],
        event={
            "event_type": "stage_advanced",
            "from_stage": state["current_stage"],
            "to_stage": next_stage,
            "assessment_reason": state["stage_assessment"]["reason"],
        },
    )
    return {
        "stage_index": next_index,
        "current_stage": next_stage,
        "state_version": result.state_version,
        "processed_operation_ids": _bounded_operations(
            state["processed_operation_ids"], operation_id
        ),
        "flow_status": "READY_FOR_NEXT_STAGE",
    }


def _finish_attempt(
    state: TeachingState, dependencies: TeachingGraphDependencies
) -> dict[str, object]:
    if not state["stage_assessment"]["passed"]:
        raise StageAdvanceError("Cannot finish without a passing final-stage assessment.")
    event_operation = state["incoming_event"]["operation_id"]
    operation_id = f"finish:{event_operation}"
    result = dependencies.event_store.persist(
        operation_id=operation_id,
        attempt_id=state["attempt_id"],
        expected_state_version=state["state_version"],
        event={
            "event_type": "attempt_completed",
            "stage": state["current_stage"],
            "formal_grade": "not_set",
        },
    )
    return {
        "state_version": result.state_version,
        "processed_operation_ids": _bounded_operations(
            state["processed_operation_ids"], operation_id
        ),
        "flow_status": "COMPLETED",
    }


def build_teaching_graph(*, dependencies: TeachingGraphDependencies, checkpointer: Any):
    """Build the explicit stage-01 graph.

    Stage 01 passes an in-memory checkpointer from tests. It is deliberately not a
    production classroom persistence default; business state still belongs in the
    event store/database boundary.
    """

    builder = StateGraph(TeachingState)
    builder.add_node("receive_event", receive_event)
    builder.add_node("validate_context", validate_context)
    builder.add_node("load_policy", load_policy)
    builder.add_node("collect_evidence", collect_evidence)
    builder.add_node("decide_action", decide_action)
    builder.add_node(
        "execute_tool", lambda state: _execute_tool(state, dependencies)
    )
    builder.add_node(
        "generate_guidance", lambda state: _generate_guidance(state, dependencies)
    )
    builder.add_node("teacher_interrupt", teacher_interrupt)
    builder.add_node(
        "persist_intervention", lambda state: _persist_intervention(state, dependencies)
    )
    builder.add_node(
        "persist_event", lambda state: _persist_event(state, dependencies)
    )
    builder.add_node("stage_assessment", stage_assessment)
    builder.add_node(
        "evaluate_requirements", lambda state: _evaluate_requirements(state, dependencies)
    )
    builder.add_node("wait", wait_state)
    builder.add_node(
        "advance_stage", lambda state: _advance_stage(state, dependencies)
    )
    builder.add_node(
        "finish_attempt", lambda state: _finish_attempt(state, dependencies)
    )

    builder.add_edge(START, "receive_event")
    builder.add_edge("receive_event", "validate_context")
    builder.add_edge("validate_context", "load_policy")
    builder.add_edge("load_policy", "collect_evidence")
    builder.add_edge("collect_evidence", "decide_action")
    builder.add_conditional_edges(
        "decide_action",
        route_decision,
        {
            "execute_tool": "execute_tool",
            "generate_guidance": "generate_guidance",
            "persist_intervention": "persist_intervention",
            "persist_event": "persist_event",
        },
    )
    builder.add_edge("execute_tool", "persist_event")
    builder.add_edge("generate_guidance", "persist_event")
    builder.add_edge("persist_intervention", "teacher_interrupt")
    builder.add_edge("teacher_interrupt", "persist_event")
    builder.add_edge("persist_event", "evaluate_requirements")
    builder.add_edge("evaluate_requirements", "stage_assessment")
    builder.add_conditional_edges(
        "stage_assessment",
        route_assessment,
        {"wait": "wait", "next": "advance_stage", "end": "finish_attempt"},
    )
    builder.add_edge("wait", END)
    builder.add_edge("advance_stage", END)
    builder.add_edge("finish_attempt", END)
    return builder.compile(checkpointer=checkpointer)


def resume_teacher(
    graph: Any, *, config: dict[str, object], resume: TeacherResume
) -> TeachingState:
    """Resume an interrupted graph through LangGraph Command after server validation."""

    return cast(TeachingState, graph.invoke(Command(resume=dict(resume)), config=config))
