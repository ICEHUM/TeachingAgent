"""Capture Stage 05B submit/recap and teacher review pages in a real browser."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CTX = json.loads((ROOT / ".runtime" / "stage05b-context.json").read_text(encoding="utf-8"))
OUT = ROOT / "reports" / "stage05b-screenshots"
OUT.mkdir(parents=True, exist_ok=True)

VIEWPORTS = [
    ("1440x900", 1440, 900),
    ("1366x768", 1366, 768),
    ("1280x800", 1280, 800),
    ("390x844", 390, 844),
]

STUDENT = f"http://127.0.0.1:5173/?user={CTX['student_id']}&attempt={CTX['attempt_id']}"
TEACHER = f"http://127.0.0.1:5174/?user={CTX['teacher_id']}"


def shot(page, name: str) -> None:
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print("wrote", path)


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        notes = []
        for label, width, height in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(STUDENT + "&view=submit", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".eval-shell, .eval-empty, .eval-loading, .app-shell", timeout=15000)
            page.wait_for_timeout(1200)
            shot(page, f"student-submit-{label}")
            page.goto(STUDENT + "&view=recap", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".eval-shell, .eval-empty, .eval-loading, .app-shell", timeout=15000)
            page.wait_for_timeout(1200)
            body = page.inner_text("body")
            notes.append({
                "page": "student-recap",
                "viewport": label,
                "has_auto": "自动检查" in body,
                "has_ai": "AI 建议" in body,
                "has_teacher": "教师确认" in body,
                "no_ai_score_field": "ai_score" not in body,
            })
            shot(page, f"student-recap-{label}")
            page.goto(STUDENT, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            shot(page, f"student-workbench-{label}")
            page.close()

            teacher = browser.new_page(viewport={"width": width, "height": height})
            teacher.goto(TEACHER, wait_until="domcontentloaded", timeout=30000)
            teacher.wait_for_timeout(1500)
            for name in ("已完成", "需要关注", "进行中"):
                tab = teacher.get_by_role("button", name=name)
                if tab.count():
                    tab.first.click()
                    teacher.wait_for_timeout(400)
            student_row = teacher.get_by_text("林雨")
            if student_row.count():
                student_row.first.click()
                teacher.wait_for_timeout(800)
            review = teacher.get_by_role("button", name="打开评价页")
            if review.count():
                review.first.click()
                teacher.wait_for_timeout(1200)
            tbody = teacher.inner_text("body")
            notes.append({
                "page": "teacher-review",
                "viewport": label,
                "has_rubric": "量规" in tbody,
                "has_auto": "自动检查" in tbody,
                "has_ai": "AI 建议" in tbody,
                "has_teacher": "教师确认" in tbody,
                "snapshot_b": "Snapshot B" in tbody,
            })
            shot(teacher, f"teacher-review-{label}")
            teacher.close()
        browser.close()
    (OUT / "qa-notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(notes, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
