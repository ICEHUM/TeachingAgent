"""Server-owned task tool catalogs backed by OpenHands RemoteWorkspace."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from app.business.python_basics import (
    HIDDEN_CASES,
    PUBLIC_CASES,
    TASK_PACKS,
    diagnose_public_case,
    task_pack_for,
)
from app.teaching_control.protocols import (
    ArtifactRef,
    EvidenceRecord,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from app.teaching_control.state import ToolCapability

from .workspace import (
    AttemptWorkspaceManager,
    WorkspaceSecurityPolicy,
    attempt_snapshot_workspace,
    container_security_facts,
    docker_command,
)

MARKER = "__TEACHING_EVIDENCE__="
MAX_SUMMARY = 512
STUDENT_EXEC_PREFIX = (
    "env -u LLM_API_KEY -u DEEPSEEK_API_KEY -u OPENAI_API_KEY "
    "-u ANTHROPIC_API_KEY -u OH_SESSION_API_KEYS_0 "
    "PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1"
)


class ToolPolicyError(RuntimeError):
    pass


class ToolResultValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    capability: ToolCapability
    timeout_seconds: int
    structured_result: bool
    requirement_kinds: tuple[str, ...]
    command: str
    cases: tuple[tuple[str, str, str], ...] = ()
    reveal_cases: bool = True


def _python_command(source: str) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return (
        f"{STUDENT_EXEC_PREFIX} python -I -c "
        f"\"import base64;exec(base64.b64decode('{encoded}'))\""
    )


INSPECT_WORKSPACE = r'''
import json
from pathlib import Path
root = Path("/workspace/student").resolve()
files = []
for item in sorted(root.rglob("*")):
    if item.is_symlink():
        raise RuntimeError("workspace_symlink_forbidden")
    if item.is_file():
        files.append({"path": item.relative_to(root).as_posix(), "size": item.stat().st_size})
        if len(files) >= 200:
            break
present = {item["path"] for item in files}
source_count = 0
try:
    sources = json.loads((root / "data/faq.json").read_text(encoding="utf-8"))
    source_count = len(sources) if isinstance(sources, list) else 0
except (OSError, json.JSONDecodeError):
    source_count = 0
checks = [
    {"code": "faq_program_present", "passed": "faq_app.py" in present},
    {"code": "faq_sources_present", "passed": "data/faq.json" in present},
    {"code": "minimum_three_sources", "passed": source_count >= 3},
]
payload = {
    "passed": all(item["passed"] for item in checks),
    "checks": checks,
    "files": files,
    "source_count": source_count,
}
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


FAQ_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path

program = Path("/workspace/student/faq_app.py")
source_path = Path("/workspace/student/data/faq.json")
checks = []
error = ""
try:
    spec = importlib.util.spec_from_file_location("student_faq_app", program)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = module.load_sources(str(source_path))
    checks.append({"code": "sources_loaded", "passed": isinstance(sources, list) and len(sources) >= 2})
    known = module.retrieve("如何重置密码？", sources)
    checks.append({"code": "known_question_hit", "passed": isinstance(known, list) and len(known) > 0})
    unknown = module.retrieve("火星基地的食堂今天供应什么？", sources)
    checks.append({"code": "unknown_question_no_fabrication", "passed": isinstance(unknown, list) and len(unknown) == 0})
    citation_ok = bool(known) and all(
        isinstance(item, dict) and bool(item.get("source") or item.get("url") or item.get("citation"))
        for item in known
    )
    checks.append({"code": "basic_citation_present", "passed": citation_ok})
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
failed = [item["code"] for item in checks if not item["passed"]]
code = "passed" if not failed else ("empty_retrieval" if "known_question_hit" in failed else failed[0])
payload = {"passed": not failed, "code": code, "checks": checks, "error": error}
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


RETRIEVAL_TESTS = FAQ_TESTS


CITATION_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path
spec = importlib.util.spec_from_file_location("student_faq_app", Path("/workspace/student/faq_app.py"))
module = importlib.util.module_from_spec(spec)
checks = []
error = ""
try:
    spec.loader.exec_module(module)
    sources = module.load_sources("/workspace/student/data/faq.json")
    hits = module.retrieve("如何重置密码？", sources)
    passed = bool(hits) and all(
        isinstance(item, dict) and bool(item.get("source") or item.get("url") or item.get("citation"))
        for item in hits
    )
    checks.append({"code": "basic_citation_present", "passed": passed})
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
payload = {"passed": all(item["passed"] for item in checks), "checks": checks, "error": error}
payload["code"] = "passed" if payload["passed"] else checks[0]["code"]
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


POLICY_FAQ_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path

program = Path("/workspace/student/faq_app.py")
source_path = Path("/workspace/student/data/faq.json")
checks = []
error = ""
try:
    spec = importlib.util.spec_from_file_location("student_policy_faq", program)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = module.load_sources(str(source_path))
    checks.append({"code": "sources_loaded", "passed": isinstance(sources, list) and len(sources) >= 3})
    known = module.retrieve("实习单位可以安排学生上夜班吗？", sources)
    known_ok = isinstance(known, list) and len(known) > 0
    checks.append({
        "code": "known_question_hit",
        "passed": known_ok,
        "detail": "已命中资料：" + ", ".join(str(item.get("id", "未命名")) for item in known)
        if known_ok else "夜班问题没有命中任何资料",
    })
    unknown = module.retrieve("火星基地的学生实习补贴是多少？", sources)
    unknown_ok = isinstance(unknown, list) and len(unknown) == 0
    unexpected_ids = [str(item.get("id", "未命名")) for item in unknown] if isinstance(unknown, list) else []
    checks.append({
        "code": "unknown_question_no_fabrication",
        "passed": unknown_ok,
        "detail": "资料外问题正确返回空结果" if unknown_ok else "资料外问题错误命中：" + ", ".join(unexpected_ids),
    })
    citation_ok = bool(known) and all(
        isinstance(item, dict)
        and bool(item.get("source"))
        and bool(item.get("authority"))
        and bool(item.get("scope"))
        for item in known
    )
    checks.append({"code": "authoritative_citation_present", "passed": citation_ok})
    local = module.retrieve("江苏省内职业学校如何落实学生实习管理？", sources)
    local_scope_ok = bool(local) and any("江苏" in str(item.get("scope", "")) for item in local)
    checks.append({"code": "local_rule_scope_preserved", "passed": local_scope_ok})
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
failed = [item["code"] for item in checks if not item["passed"]]
code = "passed" if not failed else ("empty_retrieval" if "known_question_hit" in failed else failed[0])
payload = {"passed": not failed, "code": code, "checks": checks, "error": error}
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


POLICY_ANSWER_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path

program = Path("/workspace/student/faq_app.py")
source_path = Path("/workspace/student/data/faq.json")
checks = []
error = ""
try:
    spec = importlib.util.spec_from_file_location("student_policy_faq", program)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    answer_function = getattr(module, "answer", None)
    function_ok = callable(answer_function)
    checks.append({
        "code": "answer_function_present",
        "passed": function_ok,
        "detail": "已找到 answer(question, sources)" if function_ok else "faq_app.py 中还没有 answer(question, sources)",
    })
    if function_ok:
        sources = module.load_sources(str(source_path))
        question = "实习单位可以安排学生上夜班吗？"
        hits = module.retrieve(question, sources)
        result = answer_function(question, sources)
        expected = hits[0].get("answer") if hits else None
        content_ok = isinstance(result, dict) and bool(expected) and result.get("answer") == expected
        checks.append({
            "code": "answer_uses_retrieved_content",
            "passed": content_ok,
            "detail": "回答正文来自命中资料的 answer 字段" if content_ok else "返回值中的 answer 应等于首条命中资料的 answer 字段",
        })
        citations = result.get("citations") if isinstance(result, dict) else None
        first_citation = citations[0] if isinstance(citations, list) and citations else {}
        citation_ok = (
            isinstance(first_citation, dict)
            and bool(first_citation.get("title"))
            and bool(first_citation.get("url"))
            and bool(first_citation.get("authority"))
        )
        scope_ok = isinstance(result, dict) and result.get("scope") == hits[0].get("scope") if hits else False
        checks.append({
            "code": "answer_citations_present",
            "passed": citation_ok,
            "detail": "citations 已包含标题、链接和发布机关" if citation_ok else "citations[0] 需要 title、url 和 authority",
        })
        checks.append({
            "code": "answer_scope_present",
            "passed": scope_ok,
            "detail": "scope 已保留命中资料的适用范围" if scope_ok else "scope 应等于首条命中资料的 scope 字段",
        })
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
failed = [item["code"] for item in checks if not item["passed"]]
payload = {"passed": not failed, "code": "passed" if not failed else failed[0], "checks": checks, "error": error}
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


POLICY_ANSWER_CITATION_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path

spec = importlib.util.spec_from_file_location("student_policy_faq", Path("/workspace/student/faq_app.py"))
module = importlib.util.module_from_spec(spec)
checks = []
error = ""
try:
    spec.loader.exec_module(module)
    answer_function = getattr(module, "answer", None)
    sources = module.load_sources("/workspace/student/data/faq.json")
    result = answer_function("学校可以强制学生到指定企业实习吗？", sources) if callable(answer_function) else None
    citations = result.get("citations") if isinstance(result, dict) else None
    first = citations[0] if isinstance(citations, list) and citations else {}
    citation_ok = (
        isinstance(first, dict)
        and bool(first.get("title"))
        and bool(first.get("url"))
        and bool(first.get("authority"))
    )
    scope_ok = isinstance(result, dict) and bool(result.get("scope"))
    checks.append({
        "code": "answer_citations_present",
        "passed": citation_ok,
        "detail": "citations 已包含标题、链接和发布机关" if citation_ok else "citations[0] 需要 title、url 和 authority",
    })
    checks.append({
        "code": "answer_scope_present",
        "passed": scope_ok,
        "detail": "回答已包含适用范围" if scope_ok else "返回结果需要 scope",
    })
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
failed = [item["code"] for item in checks if not item["passed"]]
payload = {"passed": not failed, "code": "passed" if not failed else failed[0], "checks": checks, "error": error}
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


POLICY_CITATION_TESTS = r'''
import importlib.util
import json
import traceback
from pathlib import Path

spec = importlib.util.spec_from_file_location("student_policy_faq", Path("/workspace/student/faq_app.py"))
module = importlib.util.module_from_spec(spec)
checks = []
error = ""
try:
    spec.loader.exec_module(module)
    sources = module.load_sources("/workspace/student/data/faq.json")
    hits = module.retrieve("学校可以强制学生到指定企业实习吗？", sources)
    citation_ok = bool(hits) and all(
        isinstance(item, dict)
        and bool(item.get("source"))
        and bool(item.get("authority"))
        and bool(item.get("scope"))
        for item in hits
    )
    checks.append({"code": "authoritative_citation_present", "passed": citation_ok})
except Exception as exc:
    checks.append({"code": "runtime_error", "passed": False, "detail": type(exc).__name__})
    error = traceback.format_exc(limit=8)[-2400:]
payload = {"passed": all(item["passed"] for item in checks), "checks": checks, "error": error}
payload["code"] = "passed" if payload["passed"] else checks[0]["code"]
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload["passed"] else 2)
'''


# These checks execute inside the same restricted workspace as the existing tools.
BRIEF_CHECK = r'''
import json
from pathlib import Path
text = Path('/workspace/student/README.md').read_text(encoding='utf-8')
passed = any(word in text.lower() for word in ('来源', '引用', 'citation', 'source'))
payload = {'passed': passed, 'code': 'passed' if passed else 'citation_rule_missing',
           'checks': [{'code': 'citation_rule_documented', 'passed': passed,
                       'detail': 'README 需说明回答如何保留来源或引用。'}]}
print('__TEACHING_EVIDENCE__=' + json.dumps(payload, ensure_ascii=False))
raise SystemExit(0 if passed else 2)
'''

DELIVERY_CHECK = r'''
import ast
import json
from pathlib import Path
root = Path('/workspace/student')
checks = []
for name in ('README.md', 'faq_app.py', 'data/faq.json'):
    path = root / name
    checks.append({'code': name, 'passed': path.is_file() and path.stat().st_size > 0,
                   'detail': '交付文件应存在且不为空。'})
readme = (root / 'README.md').read_text(encoding='utf-8') if (root / 'README.md').is_file() else ''
checks.append({'code': 'run_instructions', 'passed': 'python' in readme.lower(),
               'detail': 'README 需说明如何运行程序。'})
payload = {'passed': all(c['passed'] for c in checks), 'checks': checks}
payload['code'] = 'passed' if payload['passed'] else 'delivery_incomplete'
print('__TEACHING_EVIDENCE__=' + json.dumps(payload, ensure_ascii=False))
raise SystemExit(0 if payload['passed'] else 2)
'''

TRANSFER_CHECK = r'''
import importlib.util
import json
import traceback
from pathlib import Path
checks = []
try:
    spec = importlib.util.spec_from_file_location('transfer_faq', '/workspace/student/faq_app.py')
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    fresh = [{'id': 'library-return', 'question': '设备归还时间是什么？', 'keywords': ['设备归还', '归还时间'],
              'answer': '请在当日17点前归还。', 'source': 'https://example.invalid/library/rules',
              'source_title': '图书馆设备规则', 'authority': '教学用图书馆', 'scope': '教学示例', 'version': '1'}]
    hits = app.retrieve('设备归还时间是什么？', fresh)
    checks.append({'code': 'new_material_hit', 'passed': isinstance(hits, list) and bool(hits) and hits[0].get('id') == 'library-return', 'detail': '更换资料后仍能命中新问题。'})
    missed = app.retrieve('月球基地今天供应什么？', fresh)
    checks.append({'code': 'new_unknown_empty', 'passed': missed == [], 'detail': '新资料之外的问题应返回空列表。'})
    error = ''
except Exception:
    checks.append({'code': 'runtime_error', 'passed': False})
    error = traceback.format_exc(limit=6)[-2400:]
payload = {'passed': all(c['passed'] for c in checks), 'checks': checks, 'error': error}
payload['code'] = 'passed' if payload['passed'] else 'transfer_not_passed'
print('__TEACHING_EVIDENCE__=' + json.dumps(payload, ensure_ascii=False))
raise SystemExit(0 if payload['passed'] else 2)
'''

GENERIC_ANSWER_TESTS = r'''
import importlib.util, json, traceback
spec = importlib.util.spec_from_file_location('student_faq', '/workspace/student/faq_app.py')
module = importlib.util.module_from_spec(spec)
checks = []
error = ''
try:
    spec.loader.exec_module(module)
    sources = module.load_sources('/workspace/student/data/faq.json')
    result = module.answer('如何重置密码？', sources)
    hits = module.retrieve('如何重置密码？', sources)
    checks.append({'code': 'answer_uses_retrieved_content', 'passed': isinstance(result, dict) and bool(hits) and result.get('answer') == hits[0].get('answer')})
    citations = result.get('citations', []) if isinstance(result, dict) else []
    checks.append({'code': 'answer_citations_present', 'passed': isinstance(citations, list) and any(isinstance(c, dict) and bool(c.get('url') or c.get('source')) for c in citations)})
except Exception:
    checks.append({'code': 'runtime_error', 'passed': False})
    error = traceback.format_exc(limit=6)[-2400:]
payload = {'passed': all(c['passed'] for c in checks), 'checks': checks, 'error': error}
payload['code'] = 'passed' if payload['passed'] else 'answer_incomplete'
print('__TEACHING_EVIDENCE__=' + json.dumps(payload, ensure_ascii=False))
raise SystemExit(0 if payload['passed'] else 2)
'''

POLICY_TRANSFER_CHECK = r'''
import importlib.util, json, traceback
checks = []
error = ''
try:
    spec = importlib.util.spec_from_file_location('policy_transfer', '/workspace/student/faq_app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = module.load_sources('/workspace/student/data/faq.json')
    for question, expected in [('关于江苏的实施细则，需要保留怎样的适用范围？', '江苏'), ('关于夜班安排，需要保留怎样的适用范围？', '全国')]:
        result = module.answer(question, sources)
        checks.append({'code': 'scope_' + expected, 'passed': isinstance(result, dict) and expected in str(result.get('scope', '')), 'detail': '应区分全国规则与江苏地方细则。'})
except Exception:
    checks.append({'code': 'runtime_error', 'passed': False})
    error = traceback.format_exc(limit=6)[-2400:]
payload = {'passed': all(c['passed'] for c in checks), 'checks': checks, 'error': error}
payload['code'] = 'passed' if payload['passed'] else 'scope_transfer_failed'
print('__TEACHING_EVIDENCE__=' + json.dumps(payload, ensure_ascii=False))
raise SystemExit(0 if payload['passed'] else 2)
'''

TOOL_CATALOG: dict[str, ToolSpec] = {
    "inspect_task_brief": ToolSpec("inspect_task_brief", "DIAGNOSTIC", 30, True, ("STATIC_CHECK",), _python_command(BRIEF_CHECK)),
    "validate_transfer": ToolSpec("validate_transfer", "EVALUATION", 45, True, ("TRANSFER_TASK",), _python_command(TRANSFER_CHECK)),
    "inspect_delivery": ToolSpec("inspect_delivery", "DIAGNOSTIC", 30, True, ("STATIC_CHECK",), _python_command(DELIVERY_CHECK)),
    "validate_answer": ToolSpec("validate_answer", "EVALUATION", 45, True, ("AUTO_TEST",), _python_command(GENERIC_ANSWER_TESTS)),
    "validate_answer_citations": ToolSpec("validate_answer_citations", "EVALUATION", 45, True, ("STATIC_CHECK",), _python_command(GENERIC_ANSWER_TESTS)),
    "inspect_workspace": ToolSpec(
        "inspect_workspace",
        "DIAGNOSTIC",
        30,
        True,
        ("STATIC_CHECK",),
        _python_command(INSPECT_WORKSPACE),
    ),
    "run_student_program": ToolSpec(
        "run_student_program",
        "DIAGNOSTIC",
        30,
        False,
        (),
        f"{STUDENT_EXEC_PREFIX} python /workspace/student/faq_app.py",
    ),
    "run_faq_tests": ToolSpec(
        "run_faq_tests",
        "EVALUATION",
        45,
        True,
        ("AUTO_TEST",),
        _python_command(FAQ_TESTS),
    ),
    "validate_retrieval": ToolSpec(
        "validate_retrieval",
        "EVALUATION",
        45,
        True,
        ("AUTO_TEST",),
        _python_command(RETRIEVAL_TESTS),
    ),
    "validate_citations": ToolSpec(
        "validate_citations",
        "EVALUATION",
        45,
        True,
        ("STATIC_CHECK", "AUTO_TEST"),
        _python_command(CITATION_TESTS),
    ),
    "inspect_runtime_error": ToolSpec(
        "inspect_runtime_error",
        "DIAGNOSTIC",
        30,
        False,
        ("STATIC_CHECK",),
        _python_command(
            "from pathlib import Path; "
            "path=Path('/workspace/student/faq_app.py'); "
            "compile(path.read_text(encoding='utf-8'), str(path), 'exec')"
        ),
    ),
}


POLICY_TOOL_CATALOG: dict[str, ToolSpec] = {
    **TOOL_CATALOG,
    "validate_transfer": ToolSpec("validate_transfer", "EVALUATION", 45, True, ("TRANSFER_TASK",), _python_command(POLICY_TRANSFER_CHECK)),
    "run_faq_tests": ToolSpec(
        "run_faq_tests", "EVALUATION", 45, True, ("AUTO_TEST",),
        _python_command(POLICY_FAQ_TESTS),
    ),
    "validate_retrieval": ToolSpec(
        "validate_retrieval", "EVALUATION", 45, True, ("AUTO_TEST",),
        _python_command(POLICY_FAQ_TESTS),
    ),
    "validate_citations": ToolSpec(
        "validate_citations", "EVALUATION", 45, True, ("STATIC_CHECK", "AUTO_TEST"),
        _python_command(POLICY_CITATION_TESTS),
    ),
    "validate_answer": ToolSpec(
        "validate_answer", "EVALUATION", 45, True, ("AUTO_TEST",),
        _python_command(POLICY_ANSWER_TESTS),
    ),
    "validate_answer_citations": ToolSpec(
        "validate_answer_citations", "EVALUATION", 45, True, ("STATIC_CHECK",),
        _python_command(POLICY_ANSWER_CITATION_TESTS),
    ),
}


PYTHON_SAMPLE = r'''
import subprocess, sys
try:
    result = subprocess.run(["python", "-I", "/workspace/student/main.py"],
                            input="2\n3\n", text=True, capture_output=True, timeout=5)
except subprocess.TimeoutExpired:
    print("程序运行超过 5 秒", file=sys.stderr)
    raise SystemExit(124)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
raise SystemExit(result.returncode)
'''

PYTHON_BASIC_CATALOG: dict[str, ToolSpec] = {
    "run_python_sample": ToolSpec("run_python_sample", "DIAGNOSTIC", 20, False, (), _python_command(PYTHON_SAMPLE)),
    "run_python_trace": ToolSpec("run_python_trace", "DIAGNOSTIC", 30, True, (), ""),
    "run_python_public_tests": ToolSpec("run_python_public_tests", "EVALUATION", 45, True, ("AUTO_TEST",), "", PUBLIC_CASES),
    "run_python_hidden_tests": ToolSpec("run_python_hidden_tests", "EVALUATION", 45, True, ("AUTO_TEST",), "", HIDDEN_CASES, False),
}


def _python_sample_command(input_text: str) -> str:
    source = (
        "import subprocess, sys\n"
        "try:\n"
        " result = subprocess.run(['python', '-I', '/workspace/student/main.py'], "
        f"input={input_text!r}, text=True, capture_output=True, timeout=5)\n"
        "except subprocess.TimeoutExpired:\n"
        " print('程序运行超过 5 秒', file=sys.stderr)\n"
        " raise SystemExit(124)\n"
        "sys.stdout.write(result.stdout)\n"
        "sys.stderr.write(result.stderr)\n"
        "raise SystemExit(result.returncode)\n"
    )
    return _python_command(source)


PYTHON_CATALOGS = {"PYB-01": PYTHON_BASIC_CATALOG}
for _key, _pack in TASK_PACKS.items():
    if _key == "PYB-01":
        continue
    PYTHON_CATALOGS[_key] = {
        "run_python_sample": ToolSpec("run_python_sample", "DIAGNOSTIC", 20, False, (),
                                      _python_sample_command(_pack.public_cases[0][1])),
        "run_python_trace": ToolSpec("run_python_trace", "DIAGNOSTIC", 30, True, (), ""),
        "run_python_public_tests": ToolSpec("run_python_public_tests", "EVALUATION", 45, True,
                                            ("AUTO_TEST",), "", _pack.public_cases),
        "run_python_hidden_tests": ToolSpec("run_python_hidden_tests", "EVALUATION", 45, True,
                                            ("AUTO_TEST",), "", _pack.hidden_cases, False),
    }


def _python_case_command(input_text: str) -> str:
    # The expected answer stays in the API process. Only stdin reaches the
    # ephemeral student-code process; no test file is mounted in its workspace.
    source = (
        "import json, subprocess\n"
        "try:\n"
        " result=subprocess.run(['python','-I','/workspace/student/main.py'], "
        f"input={input_text!r}, text=True, capture_output=True, timeout=5)\n"
        " payload={'exit_code':result.returncode,'stdout':result.stdout[:4096],'stderr':result.stderr[-2400:],'timed_out':False}\n"
        "except subprocess.TimeoutExpired:\n"
        " payload={'exit_code':124,'stdout':'','stderr':'程序运行超过 5 秒','timed_out':True}\n"
        "print('__TEACHING_CASE__='+json.dumps(payload,ensure_ascii=False))\n"
    )
    return _python_command(source)


def _run_python_cases(workspace: Any, spec: ToolSpec) -> SimpleNamespace:
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    skill_evidence: list[dict[str, str]] = []
    pack = next((item for item in TASK_PACKS.values() if item.public_cases == spec.cases), None)
    for index, (label, input_text, expected) in enumerate(spec.cases, 1):
        execution = workspace.execute_command(
            _python_case_command(input_text), cwd="/workspace/student", timeout=9.0,
        )
        if execution.timeout_occurred or execution.exit_code == -1:
            return SimpleNamespace(stdout="", stderr=execution.stderr, exit_code=-1, timeout_occurred=True)
        markers = [line.removeprefix("__TEACHING_CASE__=") for line in execution.stdout.splitlines()
                   if line.startswith("__TEACHING_CASE__=")]
        if len(markers) != 1:
            raise ToolResultValidationError("python case runner returned no valid result")
        payload = json.loads(markers[0])
        actual = str(payload.get("stdout", "")).rstrip("\r\n")
        passed = payload.get("exit_code") == 0 and actual == expected
        detail = (
            f"输入 {input_text.strip().replace(chr(10), ', ')}；预期 {expected}；实际 {actual or '(无输出)'}"
            if spec.reveal_cases else "边界用例已核对；具体输入在提交前不公开。"
        )
        check = {"code": f"case_{index}" if spec.reveal_cases else "hidden_case",
                 "passed": passed, "detail": detail}
        if spec.reveal_cases and pack is not None:
            check["diagnosis_code"] = diagnose_public_case(
                task_key=pack.key, input_text=input_text, actual=actual,
                expected=expected, stderr=str(payload.get("stderr") or ""),
                exit_code=int(payload.get("exit_code") or 0),
            )
            skill_evidence.append({"skill_id": pack.public_case_skills[index - 1],
                                   "status": "verified_in_sample" if passed else "needs_review",
                                   "case_code": f"case_{index}"})
        checks.append(check)
        if not passed and payload.get("stderr") and spec.reveal_cases:
            errors.append(str(payload["stderr"])[-1200:])
    passed_all = all(item["passed"] for item in checks)
    if not spec.reveal_cases:
        checks = [{"code": "hidden_suite", "passed": passed_all,
                   "detail": "边界检查已完成；具体输入与预期结果不公开。"}]
    structured = {"passed": passed_all, "code": "passed" if passed_all else "check_failed",
                  "checks": checks, "error": "\n".join(errors)[-2400:],
                  "skill_evidence": skill_evidence}
    return SimpleNamespace(stdout=MARKER + json.dumps(structured, ensure_ascii=False),
                           stderr="", exit_code=0 if passed_all else 2, timeout_occurred=False)


def tool_catalog_for(task_version: str) -> dict[str, ToolSpec]:
    pack = task_pack_for(task_version)
    if pack is not None:
        return PYTHON_CATALOGS[pack.key]
    if task_version == "POLICY-FAQ-001-v1":
        return POLICY_TOOL_CATALOG
    if task_version == "FAQ-001-v1":
        return TOOL_CATALOG
    raise ToolPolicyError(f"unsupported task version: {task_version}")


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _structured_payload(stdout: str) -> dict[str, Any]:
    candidates = [line[len(MARKER) :] for line in stdout.splitlines() if line.startswith(MARKER)]
    if len(candidates) != 1:
        raise ToolResultValidationError("expected exactly one structured evidence marker")
    try:
        payload = json.loads(candidates[0])
    except json.JSONDecodeError as exc:
        raise ToolResultValidationError("structured evidence is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("passed"), bool):
        raise ToolResultValidationError("structured evidence has an invalid schema")
    checks = payload.get("checks", [])
    if not isinstance(checks, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("code"), str)
        and isinstance(item.get("passed"), bool)
        for item in checks
    ):
        raise ToolResultValidationError("structured checks have an invalid schema")
    return payload


def validate_execution_result(
    request: ToolExecutionRequest, result: ToolExecutionResult
) -> None:
    if result.operation_id != request.operation_id or not result.evidence:
        raise ToolResultValidationError("tool result binding is incomplete")
    for evidence in result.evidence:
        expected = (
            evidence.attempt_id == request.attempt_id
            and evidence.task_version == request.task_version
            and evidence.snapshot_id == request.snapshot_id
            and evidence.operation_id == request.operation_id
            and evidence.tool_name == request.tool_name
            and evidence.status == result.status
            and bool(evidence.artifact_refs)
        )
        if not expected:
            raise ToolResultValidationError("evidence binding does not match the request")


class OpenHandsExecutor:
    """Executes fixed server-owned catalogs in isolated OpenHands workspaces."""

    backend_name = "OpenHands"
    backend_id = "openhands"

    def __init__(self, manager: AttemptWorkspaceManager | None = None):
        self.manager = manager or AttemptWorkspaceManager()

    def _paths(self, request: ToolExecutionRequest) -> tuple[Path, Path]:
        digest = hashlib.sha256(request.operation_id.encode("utf-8")).hexdigest()
        directory = self.manager.evidence_directory(request.attempt_id)
        return directory / f"{digest}.result.json", directory / f"{digest}.artifact.json"

    def _load_duplicate(self, request: ToolExecutionRequest, path: Path) -> ToolExecutionResult:
        payload = json.loads(path.read_text(encoding="utf-8"))
        evidence_items = []
        for item in payload["evidence"]:
            item = dict(item)
            artifacts = tuple(ArtifactRef(**artifact) for artifact in item.pop("artifact_refs"))
            observed_at = datetime.fromisoformat(item.pop("observed_at"))
            evidence_items.append(
                EvidenceRecord(
                    **item,
                    artifact_refs=artifacts,
                    observed_at=observed_at,
                )
            )
        result = ToolExecutionResult(
            operation_id=payload["operation_id"],
            status=payload["status"],
            output_ref=payload["output_ref"],
            summary=payload["summary"],
            evidence=tuple(evidence_items),
            duplicate=True,
            executor_backend=payload.get("executor_backend"),
        )
        validate_execution_result(request, result)
        return result

    def _persist(
        self,
        request: ToolExecutionRequest,
        *,
        status: Literal["succeeded", "student_failure", "infrastructure_failure"],
        code: str,
        summary: str,
        stdout: str,
        stderr: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
        details: dict[str, Any],
        result_path: Path,
        artifact_path: Path,
    ) -> ToolExecutionResult:
        artifact_payload = {
            "request": {
                **asdict(request),
                "resource_policy": asdict(request.resource_policy),
            },
            "status": status,
            "code": code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "details": details,
            "executor_backend": self.backend_id,
        }
        encoded = json.dumps(
            artifact_payload, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        artifact_path.write_bytes(encoded)
        artifact_digest = hashlib.sha256(encoded).hexdigest()
        operation_digest = hashlib.sha256(request.operation_id.encode("utf-8")).hexdigest()
        attempt_digest = hashlib.sha256(request.attempt_id.encode("utf-8")).hexdigest()[:24]
        artifact = ArtifactRef(
            uri=f"artifact://{attempt_digest}/{operation_digest}",
            kind="evidence_manifest",
            sha256=artifact_digest,
            size_bytes=len(encoded),
        )
        evidence_id = hashlib.sha256(
            f"{request.attempt_id}:{request.snapshot_id}:{request.operation_id}".encode()
        ).hexdigest()
        observed_at = datetime.now(UTC)
        evidence = EvidenceRecord(
            evidence_id=evidence_id,
            attempt_id=request.attempt_id,
            task_version=request.task_version,
            snapshot_id=request.snapshot_id,
            operation_id=request.operation_id,
            tool_name=request.tool_name,
            status=status,
            code=code,
            summary=summary[:MAX_SUMMARY],
            artifact_refs=(artifact,),
            observed_at=observed_at,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        result = ToolExecutionResult(
            operation_id=request.operation_id,
            status=status,
            output_ref=f"evidence://{attempt_digest}/{evidence_id}",
            summary=summary[:MAX_SUMMARY],
            evidence=(evidence,),
            executor_backend=self.backend_id,
        )
        validate_execution_result(request, result)
        serialized = asdict(result)
        result_path.write_text(
            json.dumps(serialized, ensure_ascii=False, sort_keys=True, default=str),
            encoding="utf-8",
        )
        return result

    def _run_spec(self, request: ToolExecutionRequest, spec: ToolSpec) -> tuple[Any, dict[str, object]]:
        policy = WorkspaceSecurityPolicy(
            cpu_count=request.resource_policy.cpu_count,
            memory_mb=request.resource_policy.memory_mb,
            pids_limit=request.resource_policy.pids_limit,
        )
        with attempt_snapshot_workspace(
            manager=self.manager,
            attempt_id=request.attempt_id,
            snapshot_id=request.snapshot_id,
            policy=policy,
        ) as running:
            security_facts = container_security_facts(running.container_id)
            command_result = (
                _run_python_cases(running.workspace, spec) if spec.cases else
                running.workspace.execute_command(
                    spec.command,
                    cwd="/workspace/student",
                    timeout=float(request.timeout_seconds),
                )
            )
            if command_result.exit_code == -1:
                logs = docker_command("logs", "--tail", "20", running.container_id, check=False)
                command_result.stderr += "\nAgent Server logs:\n" + (
                    logs.stdout + logs.stderr
                )[-4096:]
        return command_result, security_facts

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        spec = tool_catalog_for(request.task_version).get(request.tool_name)
        if spec is None:
            raise ToolPolicyError("tool is not in the server-owned task catalog")
        if request.tool_capability != spec.capability:
            raise ToolPolicyError("client capability does not match the server tool catalog")
        if request.timeout_seconds <= 0 or request.timeout_seconds > spec.timeout_seconds:
            raise ToolPolicyError("tool timeout exceeds the server policy")
        if request.resource_policy.network_policy != "internal_only":
            raise ToolPolicyError("task tools require the internal-only network policy")
        result_path, artifact_path = self._paths(request)
        if result_path.exists():
            return self._load_duplicate(request, result_path)
        lock_path = result_path.with_suffix(".lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
        except FileExistsError as exc:
            if result_path.exists():
                return self._load_duplicate(request, result_path)
            raise ToolPolicyError("operation is already running") from exc
        try:
            stdout = ""
            stderr = ""
            details: dict[str, Any] = {}
            try:
                command_result, security_facts = self._run_spec(request, spec)
                stdout, stdout_truncated = _truncate(
                    command_result.stdout, request.resource_policy.max_stdout_bytes
                )
                stderr, stderr_truncated = _truncate(
                    command_result.stderr, request.resource_policy.max_stderr_bytes
                )
                stdout_truncated = stdout_truncated or bool(getattr(command_result, "stdout_truncated", False))
                stderr_truncated = stderr_truncated or bool(getattr(command_result, "stderr_truncated", False))
                details = {"container_security": security_facts}
                if command_result.timeout_occurred:
                    status = "infrastructure_failure"
                    code = "execution_timeout"
                    summary = f"{self.backend_name} terminated the tool after the server timeout."
                elif spec.structured_result:
                    try:
                        structured = _structured_payload(command_result.stdout)
                    except ToolResultValidationError as exc:
                        bounded_error, _ = _truncate(command_result.stderr, 1024)
                        raise ToolResultValidationError(
                            f"{exc}; exit_code={command_result.exit_code}; "
                            f"stderr={bounded_error!r}"
                        ) from exc
                    details = {
                        "structured": structured,
                        "container_security": security_facts,
                    }
                    status = "succeeded" if structured["passed"] else "student_failure"
                    code = str(
                        structured.get("code")
                        or ("passed" if structured["passed"] else "check_failed")
                    )
                    summary = f"{request.tool_name} {'passed' if status == 'succeeded' else 'failed'}: {code}."
                    if request.tool_name == "run_python_public_tests" and status == "student_failure":
                        diagnoses = list(dict.fromkeys(
                            str(check.get("diagnosis_code"))
                            for check in structured["checks"]
                            if not check.get("passed") and check.get("diagnosis_code")
                        ))
                        if diagnoses:
                            summary += " observed_public_diagnosis=" + ",".join(diagnoses[:3]) + "."
                elif command_result.exit_code == 0:
                    status = "succeeded"
                    code = "passed"
                    summary = f"{request.tool_name} completed successfully."
                else:
                    status = "student_failure"
                    code = "student_program_failed"
                    summary = f"{request.tool_name} returned a non-zero exit code."
                if not spec.structured_result:
                    details = {
                        "container_security": security_facts,
                        "exit_code": command_result.exit_code,
                    }
            except ToolResultValidationError:
                raise
            # This is the infrastructure boundary: Docker, HTTP transport, and
            # Agent Server startup failures are deliberately normalized here.
            except Exception as exc:  # noqa: BLE001
                stdout_truncated = False
                stderr_truncated = False
                status = "infrastructure_failure"
                code = "workspace_start_failed"
                summary = f"{self.backend_name} workspace execution failed: {type(exc).__name__}."
                details = {
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[-4096:],
                }
            return self._persist(
                request,
                status=status,
                code=code,
                summary=summary,
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                details=details,
                result_path=result_path,
                artifact_path=artifact_path,
            )
        finally:
            lock_path.unlink(missing_ok=True)


def with_duplicate(result: ToolExecutionResult) -> ToolExecutionResult:
    """Small public helper used by protocol tests."""

    return replace(result, duplicate=True)
