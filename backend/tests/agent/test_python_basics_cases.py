"""The evaluator keeps expected answers outside student execution commands."""

from __future__ import annotations

import base64
import json
import re
from types import SimpleNamespace

from app.agent.tools import MARKER, _run_python_cases, tool_catalog_for


class Workspace:
    def __init__(self, outputs: list[str]):
        self.outputs = iter(outputs)
        self.commands: list[str] = []

    def execute_command(self, command: str, **_kwargs):
        self.commands.append(command)
        stdout = next(self.outputs)
        return SimpleNamespace(
            stdout="__TEACHING_CASE__=" + json.dumps({
                "exit_code": 0, "stdout": stdout, "stderr": "", "timed_out": False,
            }),
            stderr="", exit_code=0, timeout_occurred=False,
        )


def _payload(result):
    assert result.stdout.startswith(MARKER)
    return json.loads(result.stdout[len(MARKER):])


def test_public_checks_show_reproducible_actual_output():
    workspace = Workspace(["5\n", "7\n"])
    result = _run_python_cases(workspace, tool_catalog_for("PYB-01-v1")["run_python_public_tests"])
    assert result.exit_code == 0
    payload = _payload(result)
    assert payload["passed"] is True
    assert "输入 2, 3" in payload["checks"][0]["detail"]


def test_hidden_checks_do_not_send_answers_or_reveal_cases():
    workspace = Workspace(["0\n", "0\n", "0\n"])
    result = _run_python_cases(workspace, tool_catalog_for("PYB-01-v1")["run_python_hidden_tests"])
    payload = _payload(result)
    assert payload["passed"] is False
    assert len(payload["checks"]) == 1
    assert payload["checks"][0]["code"] == "hidden_suite"
    assert "-4" not in result.stdout
    assert "912468" not in result.stdout
    assert all("实际" not in item["detail"] and "输入 " not in item["detail"]
               for item in payload["checks"])
    decoded = [base64.b64decode(re.search(r"b64decode\('([^']+)'\)", command).group(1)).decode()
               for command in workspace.commands]
    assert all("912468" not in command for command in decoded)
