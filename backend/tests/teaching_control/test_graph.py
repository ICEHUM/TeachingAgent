from __future__ import annotations

from dataclasses import fields
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.teaching_control.errors import AuthorizationError, StaleStateVersionError
from app.teaching_control.fakes import (
    FakeOpenHandsExecutor,
    FakeTeachingEventStore,
    FakeTeachingLLM,
)
from app.teaching_control.graph import (
    TeachingGraphDependencies,
    build_teaching_graph,
    resume_teacher,
)
from app.teaching_control.protocols import GuidanceDraft, ToolExecutionResult
from app.teaching_control.state import (
    DEFAULT_POLICY,
    TeachingEvent,
    TeachingState,
    new_teaching_state,
)


def make_event(
    *,
    operation_id: str,
    expected_state_version: int = 0,
    event_type: str = "check_result",
    check_passed: bool = False,
    failure_origin: str = "student",
    observation: str = "",
    requested_kind: str = "question",
    requested_tool: str = "",
    stage_requirements_met: bool = False,
) -> TeachingEvent:
    return {
        "event_id": f"event-{operation_id}",
        "event_type": event_type,
        "attempt_id": "attempt-1",
        "task_version": "faq-v1",
        "actor_id": "student-1",
        "actor_role": "student",
        "expected_state_version": expected_state_version,
        "operation_id": operation_id,
        "snapshot_id": f"snapshot-{operation_id}",
        "evidence_refs": [f"check://{operation_id}"],
        "evidence_summary": "Public check produced a bounded result summary.",
        "student_observation": observation,
        "check_passed": check_passed,
        "failure_origin": failure_origin,
        "stage_requirements_met": stage_requirements_met,
        "requested_guidance_kind": requested_kind,
        "requested_tool": requested_tool,
    }


def make_runtime():
    executor = FakeOpenHandsExecutor()
    llm = FakeTeachingLLM()
    store = FakeTeachingEventStore()
    graph = build_teaching_graph(
        dependencies=TeachingGraphDependencies(
            executor=executor,
            llm=llm,
            event_store=store,
        ),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": str(uuid4())}}
    return graph, config, executor, llm, store


def initial_state(
    event: TeachingEvent,
    *,
    mode: str = "guided_practice",
    teacher_policy=None,
) -> TeachingState:
    return new_teaching_state(
        attempt_id="attempt-1",
        task_version="faq-v1",
        learner_id="student-1",
        incoming_event=event,
        authorized_teacher_ids=["teacher-1"],
        mode=mode,
        teacher_policy=teacher_policy,
    )


def test_a_initial_failure_selects_l0():
    graph, config, _, llm, _ = make_runtime()
    result = graph.invoke(initial_state(make_event(operation_id="a")), config=config)

    assert result["student_failure_count"] == 1
    assert result["help_level"] == "L0"
    assert result["guidance"]["level"] == "L0"
    assert result["flow_status"] == "WAITING_FOR_STUDENT"
    assert len(llm.calls) == 1


def test_b_second_failure_with_valid_observation_selects_l1():
    graph, config, _, llm, _ = make_runtime()
    first = graph.invoke(initial_state(make_event(operation_id="b1")), config=config)
    second_event = make_event(
        operation_id="b2",
        expected_state_version=first["state_version"],
        observation="我观察到检索结果为空，准备检查标准化后的关键词。",
    )

    second = graph.invoke({"incoming_event": second_event}, config=config)

    assert second["student_failure_count"] == 2
    assert second["help_level"] == "L1"
    assert second["guidance"]["level"] == "L1"
    assert len(llm.calls) == 2


def test_c_failure_threshold_creates_recoverable_teacher_interrupt():
    graph, config, _, _, store = make_runtime()
    state = initial_state(make_event(operation_id="c"))
    state["student_failure_count"] = 2

    interrupted = graph.invoke(state, config=config)

    assert "__interrupt__" in interrupted
    payload = interrupted["__interrupt__"][0].value
    assert payload["attempt_id"] == "attempt-1"
    assert payload["state_version"] == 0
    assert payload["reason"] == "failure_threshold_reached"
    assert store.calls == []


def test_d_teacher_resume_revalidates_permission_and_state_version():
    graph, config, _, _, store = make_runtime()
    state = initial_state(make_event(operation_id="d"))
    state["student_failure_count"] = 2
    graph.invoke(state, config=config)
    checkpoint_state = graph.get_state(config).values

    resumed = resume_teacher(
        graph,
        config=config,
        resume={
            "teacher_id": "teacher-1",
            "roles": ["teacher"],
            "expected_state_version": checkpoint_state["state_version"],
            "response": "请学生说明匹配条件后继续。",
        },
    )

    assert resumed["teacher_resume"]["teacher_id"] == "teacher-1"
    assert resumed["pending_intervention"] is None
    assert resumed["state_version"] == 1
    assert resumed["flow_status"] == "WAITING_FOR_STUDENT"
    assert len(store.calls) == 1


def test_d_unauthorized_teacher_resume_is_rejected():
    graph, config, _, _, store = make_runtime()
    state = initial_state(make_event(operation_id="d-unauthorized"))
    state["student_failure_count"] = 2
    graph.invoke(state, config=config)
    checkpoint_state = graph.get_state(config).values

    with pytest.raises(AuthorizationError):
        resume_teacher(
            graph,
            config=config,
            resume={
                "teacher_id": "teacher-2",
                "roles": ["teacher"],
                "expected_state_version": checkpoint_state["state_version"],
                "response": "未经分配的教师不应恢复该尝试。",
            },
        )

    assert store.calls == []


def test_e_assessment_mode_rejects_answer_style_guidance():
    graph, config, executor, llm, _ = make_runtime()
    event = make_event(
        operation_id="e",
        event_type="request_guidance",
        failure_origin="none",
        requested_kind="answer",
    )

    result = graph.invoke(initial_state(event, mode="assessment"), config=config)

    assert result["flow_status"] == "REJECTED"
    assert result["error_code"] == "ANSWER_GUIDANCE_FORBIDDEN"
    assert result["guidance"] is None
    assert executor.calls == []
    assert llm.calls == []


def test_f_assessment_mode_rejects_code_patch():
    graph, config, executor, llm, _ = make_runtime()
    event = make_event(
        operation_id="f",
        event_type="run_tool",
        failure_origin="none",
        requested_kind="code_patch",
        requested_tool="apply_patch",
    )

    result = graph.invoke(initial_state(event, mode="assessment"), config=config)

    assert result["flow_status"] == "REJECTED"
    assert result["error_code"] == "CODE_PATCH_FORBIDDEN"
    assert executor.calls == []
    assert llm.calls == []


def test_g_infrastructure_failure_does_not_increment_student_failure():
    graph, config, _, llm, _ = make_runtime()
    event = make_event(operation_id="g", failure_origin="infrastructure")

    result = graph.invoke(initial_state(event), config=config)

    assert result["student_failure_count"] == 0
    assert result["infrastructure_failure_count"] == 1
    assert result["latest_check_status"] == "infrastructure_failure"
    assert llm.calls == []


def test_h_replayed_operation_id_does_not_repeat_side_effect():
    graph, config, executor, _, store = make_runtime()
    event = make_event(
        operation_id="h",
        event_type="run_tool",
        failure_origin="none",
        requested_tool="run_public_checks",
    )
    first = graph.invoke(initial_state(event), config=config)
    replay = make_event(
        operation_id="h",
        expected_state_version=first["state_version"],
        event_type="run_tool",
        failure_origin="none",
        requested_tool="run_public_checks",
    )

    second = graph.invoke({"incoming_event": replay}, config=config)

    assert len(executor.calls) == 1
    assert len(store.calls) == 1
    assert second["event_replayed"] is True
    assert second["state_version"] == first["state_version"]


def test_i_stale_state_version_resume_is_rejected():
    graph, config, _, _, _ = make_runtime()
    policy = {**DEFAULT_POLICY, "failure_threshold": 1}
    graph.invoke(
        initial_state(make_event(operation_id="i"), teacher_policy=policy),
        config=config,
    )

    with pytest.raises(StaleStateVersionError):
        resume_teacher(
            graph,
            config=config,
            resume={
                "teacher_id": "teacher-1",
                "roles": ["teacher"],
                "expected_state_version": 99,
                "response": "stale",
            },
        )


def test_j_stage_advances_only_after_passing_assessment():
    graph, config, _, _, store = make_runtime()
    not_ready = make_event(
        operation_id="j1",
        check_passed=True,
        failure_origin="none",
        stage_requirements_met=False,
    )
    first = graph.invoke(initial_state(not_ready), config=config)
    assert first["stage_index"] == 0
    assert first["stage_assessment"]["passed"] is False

    ready = make_event(
        operation_id="j2",
        expected_state_version=first["state_version"],
        check_passed=True,
        failure_origin="none",
        stage_requirements_met=True,
    )
    second = graph.invoke({"incoming_event": ready}, config=config)

    assert second["stage_assessment"]["passed"] is True
    assert second["stage_assessment"]["outcome"] == "NEXT"
    assert second["stage_index"] == 1
    assert second["current_stage"] == "prepare_sources"
    assert second["flow_status"] == "READY_FOR_NEXT_STAGE"
    assert len(store.calls) == 3


def test_state_and_adapter_contracts_exclude_forbidden_authority_and_payloads():
    forbidden_state_fields = {
        "api_key",
        "model_key",
        "workspace_token",
        "full_code",
        "raw_log",
        "formal_grade",
    }
    assert forbidden_state_fields.isdisjoint(TeachingState.__annotations__)
    assert "stage" not in {item.name for item in fields(ToolExecutionResult)}
    assert "formal_grade" not in {item.name for item in fields(GuidanceDraft)}
