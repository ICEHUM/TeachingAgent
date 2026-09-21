from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.tools import POLICY_TOOL_CATALOG, TOOL_CATALOG, tool_catalog_for
from app.business.scenario_import import ScenarioValidationError, load_scenario


ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ROOT / "scenarios" / "vocational-internship-policy-faq-v1"


def test_real_scenario_contract_is_reviewable_and_complete() -> None:
    package = load_scenario(SCENARIO)

    assert package.scenario_id == "vocational-internship-policy-faq-v1"
    assert package.task == {
        "key": "POLICY-FAQ-001",
        "title": "职业学校学生实习政策问答助手",
    }
    assert len(package.stages) == 6
    assert sum(len(stage["requirements"]) for stage in package.stages) == 11
    assert sum(item["max_score"] for item in package.rubric) == 100
    assert len(package.source_manifest) == 3
    assert len(package.policy["civic_objectives"]) == 5
    assert "不按口号表述单独评分" in package.policy["value_assessment_boundary"]
    assert set(package.workspace_files) == {
        "faq_app.py",
        "data/faq.json",
        "data/source_manifest.json",
        "README.md",
    }


def test_task_version_selects_server_owned_policy_tests() -> None:
    assert tool_catalog_for("FAQ-001-v1") is TOOL_CATALOG
    assert tool_catalog_for("POLICY-FAQ-001-v1") is POLICY_TOOL_CATALOG
    assert (
        POLICY_TOOL_CATALOG["run_faq_tests"].command
        != TOOL_CATALOG["run_faq_tests"].command
    )


def test_scenario_loader_rejects_workspace_path_traversal(tmp_path: Path) -> None:
    source = json.loads((SCENARIO / "scenario.json").read_text(encoding="utf-8"))
    source["workspace_files"] = {"../escape.py": "starter/faq_app.py"}
    (tmp_path / "scenario.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "starter").mkdir()
    (tmp_path / "starter" / "faq_app.py").write_text("", encoding="utf-8")

    with pytest.raises(ScenarioValidationError, match="unsafe workspace file path"):
        load_scenario(tmp_path)
