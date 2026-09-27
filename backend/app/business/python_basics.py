"""Server-owned, versioned first Python fundamentals task pack."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .faq import DEFAULT_TASK_POLICY

TASK_KEY = "PYB-01"
VERSION = "v1"
COURSE_CODE = "DEMO-PYTHON-BASICS-001"
STAGE_KEY = "write_program"
FILE_NAME = "main.py"
TITLE = "两数相加"
OBJECTIVE = "依次读取两行整数，只输出它们的和。输入 2 和 3 时输出 5；输入 0 和 7 时输出 7。"
STARTER = """# 改错练习：依次读取两个整数，只输出它们的和。
# 先试运行并查看终端报错，再修复代码。
first = int(input())
second = int(input())
print(first + third)
"""
PUBLIC_CASES = (("2 与 3", "2\n3\n", "5"), ("0 与 7", "0\n7\n", "7"))
HIDDEN_CASES = (("负数", "-4\n7\n", "3"), ("两个零", "0\n0\n", "0"), ("较大整数", "123456\n789012\n", "912468"))
REQUIREMENTS = (
    ("addition_public_tests", "公开样例检查", "AUTO_TEST", "python_cases_v1", "run_python_public_tests"),
    ("addition_hidden_tests", "提交边界检查", "AUTO_TEST", "python_cases_v1", "run_python_hidden_tests"),
)
RUBRIC = (("correctness", "输入、运算与输出", 70, ("addition_public_tests", "addition_hidden_tests")),
          ("process", "调试与说明", 30, ("addition_public_tests",)))

# The runtime accepts execution permissions from server code only, never from a
# task-version JSON column that a future authoring screen could modify.
POLICY = {
    **DEFAULT_TASK_POLICY,
    "allowed_tools": ["run_python_sample", "run_python_trace", "run_python_public_tests", "run_python_hidden_tests"],
    "tool_capabilities": {
        "run_python_sample": "DIAGNOSTIC",
        "run_python_trace": "DIAGNOSTIC",
        "run_python_public_tests": "EVALUATION",
        "run_python_hidden_tests": "EVALUATION",
    },
    "assessment_allowed_tools": ["run_python_sample", "run_python_trace", "run_python_public_tests", "run_python_hidden_tests"],
    "failure_counting_tools": ["run_python_public_tests", "run_python_hidden_tests"],
    "self_service_tools": ["run_python_sample", "run_python_trace"],
    "stage_objectives": {STAGE_KEY: OBJECTIVE},
    "start_stage": STAGE_KEY,
}


@dataclass(frozen=True, slots=True)
class BasicTaskPack:
    key: str
    title: str
    objective: str
    starter: str
    public_cases: tuple[tuple[str, str, str], ...]
    hidden_cases: tuple[tuple[str, str, str], ...]
    requirements: tuple[tuple[str, str, str, str, str], ...]
    rubric: tuple[tuple[str, str, int, tuple[str, ...]], ...]
    skills: tuple[str, ...]
    public_case_skills: tuple[str, ...]

    @property
    def task_version(self) -> str:
        return f"{self.key}-{VERSION}"

    @property
    def public_requirement(self) -> str:
        return self.requirements[0][0]

    @property
    def hidden_requirement(self) -> str:
        return self.requirements[1][0]


# Task rows select a pack. Executable cases and permissions always come from
# this server-owned registry, never from mutable task-version JSON.
TASK_PACKS = {
    pack.key: pack for pack in (
        BasicTaskPack(
            TASK_KEY, TITLE, OBJECTIVE, STARTER, PUBLIC_CASES, HIDDEN_CASES,
            REQUIREMENTS, RUBRIC, ("input_conversion", "integer_addition"),
            ("integer_addition", "input_conversion"),
        ),
        BasicTaskPack(
            "PYB-02", "判断是否及格",
            "读取一行 0–100 的整数分数。大于或等于 60 时只输出“及格”，否则只输出“不及格”。",
            "# 读取一名学生的分数。\n# 请用 if/else 判断是否达到 60 分，并只输出判断结果。\nscore = int(input())\n\n",
            (("59 分", "59\n", "不及格"), ("60 分", "60\n", "及格")),
            (("0 分", "0\n", "不及格"), ("100 分", "100\n", "及格"),
             ("区间内及格", "83\n", "及格"), ("区间内不及格", "41\n", "不及格")),
            (("passing_public_tests", "公开样例检查", "AUTO_TEST", "python_cases_v1", "run_python_public_tests"),
             ("passing_hidden_tests", "提交边界检查", "AUTO_TEST", "python_cases_v1", "run_python_hidden_tests")),
            (("correctness", "条件判断与边界", 70, ("passing_public_tests", "passing_hidden_tests")),
             ("process", "调试与说明", 30, ("passing_public_tests",))),
            ("if_else", "comparison_boundary"), ("if_else", "comparison_boundary"),
        ),
        BasicTaskPack(
            "PYB-03", "遍历列表求和",
            ("先读取整数个数，再逐行读取这些整数。起始代码已把它们保存到 numbers 列表。"
             "请用 for 遍历列表并只输出总和；没有数字时输出 0。"),
            ("# 输入第一行是整数个数；后面每行是一个整数。\n"
             "# 读取列表的代码已经写好，请在下方使用 for 遍历并求和。\n"
             "count = int(input())\nnumbers = []\nfor _ in range(count):\n    numbers.append(int(input()))\n\n"),
            (("三个数", "3\n2\n3\n5\n", "10"), ("空列表", "0\n", "0")),
            (("单个数", "1\n7\n", "7"), ("重复值", "3\n4\n4\n4\n", "12"),
             ("负数", "3\n-2\n5\n-1\n", "2"), ("不同长度", "4\n1\n2\n3\n4\n", "10")),
            (("traversal_public_tests", "公开样例检查", "AUTO_TEST", "python_cases_v1", "run_python_public_tests"),
             ("traversal_hidden_tests", "提交边界检查", "AUTO_TEST", "python_cases_v1", "run_python_hidden_tests")),
            (("correctness", "列表遍历与累加", 70, ("traversal_public_tests", "traversal_hidden_tests")),
             ("process", "调试与说明", 30, ("traversal_public_tests",))),
            ("for_traversal", "accumulator_initialization"),
            ("for_traversal", "accumulator_initialization"),
        ),
    )
}


def task_pack_for(task_version: str) -> BasicTaskPack | None:
    key, separator, version = task_version.rpartition("-")
    return TASK_PACKS.get(key) if separator and version == VERSION else None


def diagnose_public_case(
    *, task_key: str, input_text: str, actual: str, expected: str,
    stderr: str, exit_code: int, truncated: bool = False,
) -> str:
    """Classify only directly observed public-case failures, without guessing intent."""
    if truncated:
        return "output_truncated"
    for marker, code in (("IndentationError", "indentation_error"),
                         ("SyntaxError", "syntax_error"),
                         ("NameError", "undefined_name"),
                         ("ValueError", "input_conversion_error"),
                         ("TypeError", "type_error")):
        if marker in stderr:
            return code
    if exit_code != 0:
        return "runtime_error"
    if actual == expected:
        return "passed"
    if not actual:
        return "missing_output"
    if task_key == "PYB-01" and actual == "".join(input_text.splitlines()):
        return "string_concatenation"
    if task_key == "PYB-02" and input_text.strip() == "60":
        return "boundary_condition"
    if task_key == "PYB-03" and input_text.strip() == "0":
        return "empty_list_output"
    return "output_mismatch"


def structure_issue(task_key: str, source: str) -> str | None:
    """Check the explicitly taught construct without executing learner code."""
    if task_key not in {"PYB-02", "PYB-03"}:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None  # The executable case will report the syntax error and line.
    if task_key == "PYB-02":
        has_branch = any(isinstance(node, ast.If) and bool(node.orelse)
                         for node in ast.walk(tree))
        return None if has_branch else "missing_if_else"
    has_traversal = any(isinstance(node, ast.For)
                        and isinstance(node.iter, ast.Name)
                        and node.iter.id == "numbers"
                        for node in ast.walk(tree))
    return None if has_traversal else "missing_for_traversal"
