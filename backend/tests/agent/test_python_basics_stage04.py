"""The three Python task packs share a runner contract without leaking hidden cases."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent.tools import MARKER, _run_python_cases, tool_catalog_for
from app.business.python_basics import (
    TASK_PACKS,
    diagnose_public_case,
    structure_issue,
    task_pack_for,
)


@pytest.mark.parametrize("task_key", ["PYB-01", "PYB-02", "PYB-03"])
def test_public_and_hidden_catalogs_are_server_owned(task_key: str) -> None:
    pack = TASK_PACKS[task_key]
    assert task_pack_for(pack.task_version) is pack
    catalog = tool_catalog_for(pack.task_version)
    assert catalog["run_python_public_tests"].cases == pack.public_cases
    assert catalog["run_python_hidden_tests"].cases == pack.hidden_cases
    assert catalog["run_python_hidden_tests"].reveal_cases is False
    assert len(pack.public_case_skills) == len(pack.public_cases)
    assert task_pack_for(f"{task_key}-v99") is None


class CaseWorkspace:
    def __init__(self, outputs: list[str]):
        self.outputs = iter(outputs)

    def execute_command(self, _command: str, **_kwargs):
        value = next(self.outputs)
        return SimpleNamespace(
            stdout="__TEACHING_CASE__=" + json.dumps({
                "exit_code": 0, "stdout": value, "stderr": "", "timed_out": False,
            }), stderr="", exit_code=0, timeout_occurred=False,
        )


@pytest.mark.parametrize("task_key", ["PYB-02", "PYB-03"])
def test_public_cases_record_observed_skill_evidence(task_key: str) -> None:
    pack = TASK_PACKS[task_key]
    output = [expected + "\n" for _label, _input, expected in pack.public_cases]
    result = _run_python_cases(CaseWorkspace(output), tool_catalog_for(pack.task_version)["run_python_public_tests"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.removeprefix(MARKER))
    assert payload["passed"] is True
    assert [item["skill_id"] for item in payload["skill_evidence"]] == list(pack.public_case_skills)
    assert all(item["status"] == "verified_in_sample" for item in payload["skill_evidence"])


@pytest.mark.parametrize("task_key", ["PYB-02", "PYB-03"])
def test_hidden_cases_return_only_a_suite_status(task_key: str) -> None:
    pack = TASK_PACKS[task_key]
    result = _run_python_cases(
        CaseWorkspace(["wrong\n"] * len(pack.hidden_cases)),
        tool_catalog_for(pack.task_version)["run_python_hidden_tests"],
    )
    payload = json.loads(result.stdout.removeprefix(MARKER))
    assert payload["checks"] == [{"code": "hidden_suite", "passed": False,
                                   "detail": "边界检查已完成；具体输入与预期结果不公开。"}]
    assert payload["skill_evidence"] == []
    assert all(input_text.strip() not in result.stdout for _label, input_text, _expected in pack.hidden_cases)


def test_public_diagnosis_uses_observed_difference() -> None:
    assert diagnose_public_case(task_key="PYB-02", input_text="60\n", actual="不及格",
                                expected="及格", stderr="", exit_code=0) == "boundary_condition"
    assert diagnose_public_case(task_key="PYB-03", input_text="0\n", actual="",
                                expected="0", stderr="", exit_code=0) == "missing_output"
    assert diagnose_public_case(task_key="PYB-03", input_text="3\n2\n3\n5\n", actual="",
                                expected="10", stderr="Traceback: IndentationError", exit_code=1) == "indentation_error"


def test_task_structure_checks_match_the_taught_construct() -> None:
    assert structure_issue("PYB-02", "score=int(input())\nprint('及格' if score >= 60 else '不及格')\n") == "missing_if_else"
    assert structure_issue("PYB-02", "if score >= 60:\n    print('及格')\nelse:\n    print('不及格')\n") is None
    assert structure_issue("PYB-03", "for _ in range(count):\n    numbers.append(int(input()))\nprint(sum(numbers))\n") == "missing_for_traversal"
    assert structure_issue("PYB-03", "total=0\nfor number in numbers:\n    total += number\nprint(total)\n") is None
