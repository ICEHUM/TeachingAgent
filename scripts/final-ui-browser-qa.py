"""Capture the four locked core views and run lightweight browser accessibility QA."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "final-ui-polish"
OUT.mkdir(parents=True, exist_ok=True)
CONTEXT = json.loads((ROOT / ".runtime" / "stage05a-context.json").read_text(encoding="utf-8"))
VIEWPORTS = [("1440x900", 1440, 900), ("1366x768", 1366, 768), ("1280x800", 1280, 800), ("390x844", 390, 844)]
STUDENT = (
    "http://127.0.0.1:5173/"
    f"?user={CONTEXT['teacher_case']['user_id']}&attempt={CONTEXT['teacher_case']['attempt_id']}"
)
TEACHER = f"http://127.0.0.1:5174/?user={CONTEXT['teacher_id']}"


def health(page: Page) -> dict:
    return page.evaluate("""() => {
      const controls = [...document.querySelectorAll('button, a, input, textarea, select')]
        .filter((node) => {
          const rect = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          return !node.disabled && node.getAttribute('aria-hidden') !== 'true'
            && rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden';
        });
      const unnamed = controls.filter((node) => {
        const labels = node.labels ? [...node.labels].map((label) => label.innerText).join(' ') : '';
        return !(node.getAttribute('aria-label') || labels || node.innerText || node.getAttribute('title'))?.trim();
      });
      const tiny = [...document.querySelectorAll('body *')].filter((node) => {
        const style = getComputedStyle(node);
        return node.children.length === 0 && node.textContent.trim() && parseFloat(style.fontSize) < 12;
      });
      return {
        viewport: [window.innerWidth, window.innerHeight],
        horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
        interactiveControls: controls.length,
        unnamedControls: unnamed.length,
        textBelow12px: tiny.length
      };
    }""")


def focus_path(page: Page, steps: int = 10) -> list[dict]:
    page.locator("body").click(position={"x": 2, "y": 2})
    result: list[dict] = []
    for _ in range(steps):
        page.keyboard.press("Tab")
        item = page.evaluate("""() => {
          const node = document.activeElement;
          if (!node || node === document.body) return null;
          const style = getComputedStyle(node);
          return {
            tag: node.tagName,
            name: (node.getAttribute('aria-label') || node.innerText || node.getAttribute('placeholder') || '').trim().slice(0, 80),
            visibleFocus: style.outlineStyle !== 'none' || style.boxShadow !== 'none'
          };
        }""")
        if item and item["tag"] != "BODY":
            result.append(item)
    return result


def record(notes: list[dict], page: Page, *, name: str, viewport: str) -> None:
    item = {"page": name, "viewport": viewport, **health(page)}
    if viewport == "1440x900":
        item["keyboardPath"] = focus_path(page)
    notes.append(item)


def capture_student(browser, notes: list[dict], label: str, width: int, height: int) -> None:
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(STUDENT, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(".workspace-grid", timeout=20000)
    page.wait_for_timeout(800)
    body = page.locator("body").inner_text()
    assert all(text in body for text in ("运行检查", "实训教练", "等待教师"))
    page.screenshot(path=str(OUT / f"student-workbench-{label}.png"), full_page=False)
    record(notes, page, name="student-workbench", viewport=label)

    requirements = page.get_by_role("tab", name="验收 3/4")
    if not requirements.count():
        requirements = page.get_by_role("tab", name="验收", exact=False)
    requirements.first.click()
    page.wait_for_selector(".requirement-checklist", timeout=10000)
    evidence_item = page.locator(".checklist-item:not([disabled])").first
    assert evidence_item.count(), "no real evidence is available for the drawer"
    evidence_item.click()
    page.wait_for_selector(".evidence-drawer", timeout=10000)
    page.wait_for_timeout(400)
    drawer_text = page.locator(".evidence-drawer").inner_text()
    assert all(text in drawer_text for text in ("教学结论", "失败原因", "技术追溯"))
    page.screenshot(path=str(OUT / f"evidence-drawer-{label}.png"), full_page=False)
    record(notes, page, name="evidence-drawer", viewport=label)
    page.keyboard.press("Escape")
    page.wait_for_selector(".evidence-drawer", state="detached", timeout=5000)
    page.close()


def capture_teacher(browser, notes: list[dict], label: str, width: int, height: int) -> None:
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(TEACHER, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(".teacher-layout", timeout=20000)
    page.wait_for_timeout(800)
    classroom = page.locator("body").inner_text()
    assert all(text in classroom for text in ("现在谁需要教师帮助", "需要关注", "周宁"))
    page.screenshot(path=str(OUT / f"teacher-classroom-{label}.png"), full_page=False)
    record(notes, page, name="teacher-classroom", viewport=label)

    row = page.locator(".student-row").filter(has_text="周宁")
    assert row.count(), "active intervention row is missing"
    row.first.click()
    page.wait_for_selector(".detail-head", timeout=15000)
    page.wait_for_timeout(500)
    action = page.locator(".action-toggle")
    if action.count() and action.get_attribute("aria-expanded") != "true":
        action.click()
        page.wait_for_timeout(350)
    panel = page.locator(".intervention-panel").inner_text()
    assert all(text in panel for text in ("教师介入说明", "指导策略", "流程控制", "待处理"))
    page.screenshot(path=str(OUT / f"intervention-panel-{label}.png"), full_page=False)
    record(notes, page, name="intervention-panel", viewport=label)
    if width < 768:
        page.keyboard.press("Escape")
    page.close()


def main() -> None:
    notes: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        for label, width, height in VIEWPORTS:
            capture_student(browser, notes, label, width, height)
            capture_teacher(browser, notes, label, width, height)
        browser.close()
    report = {
        "viewports": notes,
        "passed": all(
            not item["horizontalOverflow"]
            and item["unnamedControls"] == 0
            and item["textBelow12px"] == 0
            and all(step["visibleFocus"] for step in item.get("keyboardPath", []))
            for item in notes
        ),
    }
    (OUT / "core-browser-qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
