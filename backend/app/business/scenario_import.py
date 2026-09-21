"""Validated, declarative teaching-scenario packages.

Stage 06B intentionally supports one real classroom scenario.  The loader is
kept independent from the database so a package can be reviewed and validated
before a guarded DEMO/TEST import writes any business facts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ScenarioValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScenarioPackage:
    root: Path
    scenario_id: str
    course: dict[str, str]
    task: dict[str, str]
    version: str
    policy: dict[str, Any]
    stages: tuple[dict[str, Any], ...]
    rubric: tuple[dict[str, Any], ...]
    workspace_files: dict[str, str]
    source_manifest: tuple[dict[str, Any], ...]


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _safe_relative_path(value: Any) -> str:
    raw = _required_text(value, "workspace file path").replace("\\", "/")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or raw.startswith("/"):
        raise ScenarioValidationError(f"unsafe workspace file path: {raw}")
    return raw


def load_scenario(root: Path) -> ScenarioPackage:
    root = root.resolve()
    manifest_path = root / "scenario.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioValidationError(f"cannot read scenario.json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScenarioValidationError("scenario.json must contain an object")

    scenario_id = _required_text(payload.get("scenario_id"), "scenario_id")
    course = payload.get("course")
    task = payload.get("task")
    if not isinstance(course, dict) or not isinstance(task, dict):
        raise ScenarioValidationError("course and task must be objects")
    course = {
        "code": _required_text(course.get("code"), "course.code"),
        "name": _required_text(course.get("name"), "course.name"),
    }
    task = {
        "key": _required_text(task.get("key"), "task.key"),
        "title": _required_text(task.get("title"), "task.title"),
    }
    version = _required_text(payload.get("version"), "version")
    policy = payload.get("policy")
    stages = payload.get("stages")
    rubric = payload.get("rubric")
    files = payload.get("workspace_files")
    sources = payload.get("source_manifest")
    if not isinstance(policy, dict):
        raise ScenarioValidationError("policy must be an object")
    if not isinstance(stages, list) or not stages:
        raise ScenarioValidationError("stages must be a non-empty array")
    if not isinstance(rubric, list) or not rubric:
        raise ScenarioValidationError("rubric must be a non-empty array")
    if not isinstance(files, dict) or not files:
        raise ScenarioValidationError("workspace_files must be a non-empty object")
    if not isinstance(sources, list) or len(sources) < 2:
        raise ScenarioValidationError("source_manifest must contain at least two sources")

    stage_keys: set[str] = set()
    requirement_keys: set[str] = set()
    normalized_stages: list[dict[str, Any]] = []
    for position, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise ScenarioValidationError(f"stages[{position}] must be an object")
        key = _required_text(stage.get("key"), f"stages[{position}].key")
        if key in stage_keys:
            raise ScenarioValidationError(f"duplicate stage key: {key}")
        stage_keys.add(key)
        requirements = stage.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ScenarioValidationError(f"stage {key} has no requirements")
        normalized_requirements: list[dict[str, Any]] = []
        for index, requirement in enumerate(requirements):
            if not isinstance(requirement, dict):
                raise ScenarioValidationError(f"{key}.requirements[{index}] must be an object")
            req_key = _required_text(requirement.get("key"), f"{key}.requirements[{index}].key")
            if req_key in requirement_keys:
                raise ScenarioValidationError(f"duplicate requirement key: {req_key}")
            requirement_keys.add(req_key)
            normalized_requirements.append({
                "key": req_key,
                "name": _required_text(requirement.get("name"), f"requirement {req_key}.name"),
                "kind": _required_text(requirement.get("kind"), f"requirement {req_key}.kind"),
                "evaluator": _required_text(requirement.get("evaluator"), f"requirement {req_key}.evaluator"),
                "required": bool(requirement.get("required", True)),
                "config": dict(requirement.get("config") or {}),
            })
        normalized_stages.append({
            "key": key,
            "title": _required_text(stage.get("title"), f"stage {key}.title"),
            "objective": _required_text(stage.get("objective"), f"stage {key}.objective"),
            "aggregation": _required_text(stage.get("aggregation", "ALL_REQUIRED"), f"stage {key}.aggregation"),
            "requirements": normalized_requirements,
        })

    normalized_rubric: list[dict[str, Any]] = []
    for index, item in enumerate(rubric):
        if not isinstance(item, dict):
            raise ScenarioValidationError(f"rubric[{index}] must be an object")
        refs = item.get("requirement_keys")
        if not isinstance(refs, list) or not refs or any(ref not in requirement_keys for ref in refs):
            raise ScenarioValidationError(f"rubric[{index}] contains unknown requirements")
        normalized_rubric.append({
            "key": _required_text(item.get("key"), f"rubric[{index}].key"),
            "title": _required_text(item.get("title"), f"rubric[{index}].title"),
            "max_score": int(item.get("max_score", 0)),
            "requirement_keys": list(refs),
        })
    if sum(item["max_score"] for item in normalized_rubric) != 100:
        raise ScenarioValidationError("rubric max_score must total 100")

    workspace_files: dict[str, str] = {}
    for target, source in files.items():
        target = _safe_relative_path(target)
        source = _safe_relative_path(source)
        source_path = (root / source).resolve()
        if root not in source_path.parents or not source_path.is_file():
            raise ScenarioValidationError(f"workspace source does not exist: {source}")
        workspace_files[target] = source

    normalized_sources: list[dict[str, Any]] = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ScenarioValidationError(f"source_manifest[{index}] must be an object")
        normalized_sources.append({
            "id": _required_text(source.get("id"), f"source_manifest[{index}].id"),
            "title": _required_text(source.get("title"), f"source_manifest[{index}].title"),
            "url": _required_text(source.get("url"), f"source_manifest[{index}].url"),
            "authority": _required_text(source.get("authority"), f"source_manifest[{index}].authority"),
            "scope": _required_text(source.get("scope"), f"source_manifest[{index}].scope"),
        })

    return ScenarioPackage(
        root=root,
        scenario_id=scenario_id,
        course=course,
        task=task,
        version=version,
        policy=dict(policy),
        stages=tuple(normalized_stages),
        rubric=tuple(normalized_rubric),
        workspace_files=workspace_files,
        source_manifest=tuple(normalized_sources),
    )
