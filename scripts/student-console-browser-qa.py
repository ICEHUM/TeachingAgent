"""Verify the student self-service console in a real browser (Microsoft Edge).

Covers: console tab, "运行我的程序", raw stdout/stderr, advisory runs not counting as
learning failures, the clickable error location, the untouched acceptance path, four
viewports and keyboard activation.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "student-console"
OUT.mkdir(parents=True, exist_ok=True)
API = "http://127.0.0.1:8000"
STUDENT = "http://127.0.0.1:5173"
LOGIN = json.loads(
    (Path(os.environ["TEMP"]) / "teachingagent-stage06-demo-login.json").read_text(encoding="utf-8")
)
VIEWPORTS = [("1440x900", 1440, 900), ("1366x768", 1366, 768), ("1024x768", 1024, 768), ("390x844", 390, 844)]
RUN_TIMEOUT_MS = 240_000

WORKING = (
    "# QA 控制台验证用的可运行版本。\n"
    "import json\nfrom pathlib import Path\n\n\n"
    "def load_sources(path: str) -> list[dict]:\n"
    '    return json.loads(Path(path).read_text(encoding="utf-8"))\n\n\n'
    "def retrieve(question: str, sources: list[dict]) -> list[dict]:\n"
    "    return [item for item in sources if '实习' in item.get('question', '')]\n\n\n"
    'if __name__ == "__main__":\n'
    '    data = load_sources("data/faq.json")\n'
    '    print("sources:", len(data))\n'
    '    print("hits:", len(retrieve("实习单位可以安排学生上夜班吗？", data)))\n'
)

BROKEN_LINE = "    print(retrieve_answer(data))"
BROKEN = (
    "# QA 控制台验证用的报错版本。\n"
    + "\n".join(f"# 填充行 {index}" for index in range(1, 38))
    + '\nfrom __future__ import annotations\n\nimport json\nfrom pathlib import Path\n\n\n'
    'def load_sources(path: str) -> list[dict]:\n'
    '    return json.loads(Path(path).read_text(encoding="utf-8"))\n\n\n'
    'def retrieve(question: str, sources: list[dict]) -> list[dict]:\n'
    '    return []\n\n\n'
    'if __name__ == "__main__":\n'
    '    data = load_sources("data/faq.json")\n'
    + BROKEN_LINE
    + "\n"
)


def api(method: str, path: str, *, user_id: str | None = None, body: dict | None = None) -> dict:
    request = urllib.request.Request(API + path, method=method)
    if user_id:
        request.add_header("X-User-Id", user_id)
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, data=payload, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def put_source(user_id: str, attempt_id: str, content: str) -> str:
    current = api("GET", f"/api/product/attempts/{attempt_id}/files/faq_app.py", user_id=user_id)
    saved = api(
        "PUT",
        f"/api/product/attempts/{attempt_id}/files/faq_app.py",
        user_id=user_id,
        body={"content": content, "expected_hash": current["hash"]},
    )
    return saved["hash"]


def new_snapshot(user_id: str, attempt_id: str, content_hash: str, operation: str) -> dict:
    return api(
        "POST",
        f"/api/product/attempts/{attempt_id}/snapshots",
        user_id=user_id,
        body={"operation_id": operation, "expected_file_hash": content_hash, "expected_file_path": "faq_app.py"},
    )


def state(user_id: str, attempt_id: str) -> dict:
    workbench = api("GET", f"/api/product/attempts/{attempt_id}/workbench", user_id=user_id)
    return {
        "state_version": workbench["attempt"]["state_version"],
        "failures": workbench["attempt"]["student_failure_count"],
        "infra": workbench["attempt"]["infrastructure_failure_count"],
        "snapshot": workbench["latest_snapshot"]["label"] if workbench["latest_snapshot"] else None,
        "timeline": [item["label"] for item in workbench["timeline"]][-4:],
    }


def resolve_open_intervention(teacher_id: str, attempt_id: str, operation: str) -> str | None:
    """Classroom action: the teacher answers a pending help request so the learner can go on."""
    workbench = api("GET", f"/api/product/attempts/{attempt_id}/workbench", user_id=teacher_id)
    intervention = workbench.get("intervention")
    if not intervention or intervention.get("status") != "WAITING_TEACHER":
        return None
    api(
        "POST",
        f"/api/product/teacher/interventions/{intervention['id']}/action",
        user_id=teacher_id,
        body={
            "operation_id": operation,
            "expected_state_version": workbench["attempt"]["state_version"],
            "action": "continue",
            "teacher_prompt": "先自己运行程序看清报错，再决定是否需要帮助。",
        },
    )
    return intervention["id"]


def open_console(page) -> None:
    page.locator(".result-head .tabs button").filter(has_text="输出").first.click()
    page.wait_for_selector(".console-pane", timeout=15_000)


def wait_for_run(page) -> None:
    """The run always ends in either a console payload or an explicit failure state."""
    page.wait_for_selector(".console-meta, .console-status.failed", timeout=RUN_TIMEOUT_MS)


def console_failed(page) -> bool:
    return page.locator(".console-status.failed").count() > 0


def main() -> None:
    notes: list[dict] = []
    failures: list[str] = []
    run_tag = uuid.uuid4().hex[:8]

    def check(name: str, ok: bool, detail: str = "") -> None:
        notes.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}")

    student_login = api(
        "POST",
        "/api/product/auth/demo-login",
        body={"account": LOGIN["student_account"], "password": LOGIN["student_password"]},
    )
    teacher_login = api(
        "POST",
        "/api/product/auth/demo-login",
        body={"account": LOGIN["teacher_account"], "password": LOGIN["teacher_password"]},
    )
    user_id, attempt_id = student_login["user_id"], student_login["attempt_id"]
    teacher_id = teacher_login["user_id"]

    opened = resolve_open_intervention(teacher_id, attempt_id, f"qa-console-teacher-open-{run_tag}")
    if opened:
        notes.append({"check": "resolved the pending intervention before the checks", "ok": True, "detail": opened})
    pending = api("GET", f"/api/product/attempts/{attempt_id}/workbench", user_id=user_id).get("intervention")
    check(
        "learner is unblocked before the console checks",
        not pending or pending.get("status") != "WAITING_TEACHER",
        json.dumps(pending, ensure_ascii=False),
    )

    workbench = api("GET", f"/api/product/attempts/{attempt_id}/workbench", user_id=user_id)
    tool_names = [item["name"] for item in workbench["self_service_tools"]]
    check(
        "workbench exposes the server-owned console tools",
        tool_names == ["run_student_program", "inspect_runtime_error"],
        ",".join(tool_names),
    )
    baseline_hash = put_source(user_id, attempt_id, WORKING)
    new_snapshot(user_id, attempt_id, baseline_hash, f"qa-baseline-{run_tag}")
    expected_line = BROKEN.splitlines().index(BROKEN_LINE) + 1

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(STUDENT, wait_until="domcontentloaded", timeout=30_000)
        page.get_by_label("账号").fill(LOGIN["student_account"])
        page.get_by_label("密码").fill(LOGIN["student_password"])
        page.get_by_role("button", name="进入教学空间").click()
        page.wait_for_selector(".app-shell", timeout=30_000)
        page.wait_for_timeout(1500)
        check("demo login reaches the workbench", page.locator(".app-shell").count() == 1)
        check(
            "toolbar shows the run-program action",
            page.get_by_role("button", name="运行我的程序").count() >= 1,
            "toolbar button",
        )

        open_console(page)
        empty_text = page.inner_text(".console-status")
        check(
            "console empty state explains what a run does",
            "还没有运行记录" in empty_text and "运行我的程序" in empty_text,
            empty_text[:80],
        )
        page.screenshot(path=str(OUT / "console-empty-1440x900.png"))

        before = state(user_id, attempt_id)
        page.get_by_role("button", name="运行我的程序").first.click()
        wait_for_run(page)
        stdout = page.inner_text(".console-stream pre")
        status_text = page.inner_text(".console-status")
        check("self run reaches a terminal state", "程序正常结束" in status_text, status_text[:80])
        check("stdout shows the program's own output", "sources:" in stdout, stdout[:120])
        check("console records an operation reference", "已记录为运行证据" in page.inner_text(".console-footnote"))
        after_normal = state(user_id, attempt_id)
        check(
            "successful self run does not change failure counters",
            (after_normal["failures"], after_normal["infra"]) == (before["failures"], before["infra"]),
            f"{before['failures']}/{before['infra']} -> {after_normal['failures']}/{after_normal['infra']}",
        )
        check("successful self run advances state", after_normal["state_version"] > before["state_version"])
        page.screenshot(path=str(OUT / "console-run-1440x900.png"))

        put_source(user_id, attempt_id, BROKEN)
        new_snapshot(user_id, attempt_id, hashlib.sha256(BROKEN.encode()).hexdigest(), f"qa-broken-{run_tag}")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".app-shell", timeout=30_000)
        page.wait_for_timeout(1200)
        page.locator(".file-list button").filter(has_text="README.md").first.click()
        page.wait_for_timeout(600)
        open_console(page)
        page.get_by_role("button", name="运行我的程序").first.click()
        wait_for_run(page)
        stderr = page.inner_text(".console-stream.error pre")
        check(
            "broken program shows the real traceback",
            "NameError" in stderr and "faq_app.py" in stderr,
            stderr.splitlines()[-1][:80],
        )
        locate = page.locator(".console-locate")
        check(
            "console offers a jump to the failing line",
            locate.count() == 1,
            locate.first.inner_text() if locate.count() else "missing",
        )
        page.screenshot(path=str(OUT / "console-error-1440x900.png"))
        locate.first.click()
        page.wait_for_timeout(1500)
        active_file = page.inner_text(".editor-tab").strip()
        focused_in_editor = page.evaluate(
            "() => { const el = document.activeElement; return !!(el && el.closest && el.closest('.monaco-editor')); }"
        )
        visible_lines = page.evaluate(
            "() => Array.from(document.querySelectorAll('.margin-view-overlays .line-numbers')).map((node) => node.textContent)"
        )
        check("jump switches to the failing file", active_file == "faq_app.py", active_file)
        check("jump puts the cursor in the editor", focused_in_editor)
        check(
            f"jump reveals line {expected_line}",
            any((text or "").strip() == str(expected_line) for text in visible_lines),
            f"gutter shows: {visible_lines[:6]}..{visible_lines[-3:]}",
        )
        after_broken = state(user_id, attempt_id)
        check(
            "failing self run is not counted as a learning failure",
            after_broken["failures"] == before["failures"],
            f"{before['failures']} -> {after_broken['failures']}",
        )
        check(
            "timeline distinguishes a self run from a graded check",
            any("自己运行程序" in label for label in after_broken["timeline"]),
            " | ".join(after_broken["timeline"]),
        )
        page.screenshot(path=str(OUT / "console-located-1440x900.png"))

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".app-shell", timeout=30_000)
        page.wait_for_timeout(1500)
        if page.locator(".teacher-banner").count():
            resolve_open_intervention(teacher_id, attempt_id, f"qa-console-teacher-keyboard-{run_tag}")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".app-shell", timeout=30_000)
            page.wait_for_timeout(1500)
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('.toolbar-actions button'))"
            ".some((button) => !button.disabled && button.textContent.includes('运行我的程序'))",
            timeout=20_000,
        )
        hit = False
        focus_trail: list[str] = []
        for _ in range(30):
            page.keyboard.press("Tab")
            focused = (
                page.evaluate("() => document.activeElement ? document.activeElement.textContent : ''") or ""
            ).strip()
            focus_trail.append(focused[:16] or "-")
            if "运行我的程序" in focused:
                hit = True
                break
        check("run-program action is keyboard reachable", hit, " > ".join(focus_trail))
        if hit:
            page.keyboard.press("Enter")
            started = False
            try:
                page.wait_for_selector(".console-status.running, .console-meta", timeout=15_000)
                started = True
            except Exception:  # noqa: BLE001
                started = False
            check("keyboard activation starts the run", started)
            if started:
                wait_for_run(page)
                check("keyboard-started run reaches a terminal state", not console_failed(page))

        page.locator(".result-head .tabs button").filter(has_text="运行结果").first.click()
        page.get_by_role("button", name="运行检查").first.click()
        page.wait_for_function(
            "() => { const node = document.querySelector('.run-state strong'); return !!node && node.textContent.includes('检查'); }",
            timeout=RUN_TIMEOUT_MS,
        )
        result_text = page.inner_text(".run-state")
        check("acceptance run still reports its own result", "本次代码" in result_text, result_text[:100])
        after_check = state(user_id, attempt_id)
        graded_failed = "检查未通过" in result_text or "运行错误" in result_text
        check(
            "graded check keeps its own counting rule",
            after_check["failures"] == after_broken["failures"] + (1 if graded_failed else 0),
            f"{after_broken['failures']} -> {after_check['failures']} (failed={graded_failed})",
        )
        page.screenshot(path=str(OUT / "acceptance-run-1440x900.png"))
        page.close()

        reopened = resolve_open_intervention(teacher_id, attempt_id, f"qa-console-teacher-views-{run_tag}")
        if reopened:
            notes.append(
                {
                    "check": "resolved the intervention raised by the graded check",
                    "ok": True,
                    "detail": reopened,
                }
            )

        for label, width, height in VIEWPORTS:
            view = browser.new_page(viewport={"width": width, "height": height})
            view.goto(
                f"{STUDENT}/?user={user_id}&attempt={attempt_id}",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            view.wait_for_selector(".app-shell", timeout=30_000)
            view.wait_for_timeout(1200)
            open_console(view)
            view.wait_for_timeout(400)
            metrics = view.evaluate(
                "() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth })"
            )
            check(
                f"no page-level horizontal overflow at {label}",
                metrics["scrollWidth"] <= metrics["clientWidth"] + 1,
                json.dumps(metrics),
            )
            check(
                f"console pane renders at {label}",
                view.locator(".console-pane").count() == 1 and view.locator(".console-status").count() >= 1,
            )
            view.screenshot(path=str(OUT / f"console-{label}.png"))
            view.close()

        browser.close()

    left_open = resolve_open_intervention(teacher_id, attempt_id, f"qa-console-teacher-close-{run_tag}")
    notes.append(
        {
            "check": "demo attempt left without a pending intervention",
            "ok": True,
            "detail": left_open or "none pending",
        }
    )
    (OUT / "qa-notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"passed": not failures, "failures": failures, "checks": len(notes)},
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
