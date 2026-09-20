from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CTX = json.loads((ROOT / ".runtime" / "stage05b-context.json").read_text(encoding="utf-8"))
OUT = ROOT / "reports" / "stage05b-screenshots"
TEACHER = f"http://127.0.0.1:5174/?user={CTX['teacher_id']}"
VIEWPORTS = [("1440x900", 1440, 900), ("1366x768", 1366, 768), ("1280x800", 1280, 800), ("390x844", 390, 844)]


def main() -> None:
    notes = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        for label, width, height in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(TEACHER, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            page.locator("nav.group-tabs button").filter(has_text="已完成").click()
            page.wait_for_timeout(800)
            rows = page.locator(".student-row")
            print("completed_rows", rows.count(), page.inner_text(".student-list")[:500])
            rows.first.click()
            page.wait_for_timeout(1500)
            print("detail_head", page.locator(".detail-head").inner_text())
            for name in ("查看评价", "评价作品", "打开评价页"):
                btn = page.get_by_role("button", name=name)
                if btn.count():
                    btn.first.click()
                    break
            else:
                raise RuntimeError("no review button: " + page.inner_text("aside")[:800])
            page.wait_for_timeout(1500)
            page.wait_for_selector(".eval-shell, .rubric-card, .eval-empty", timeout=10000)
            body = page.inner_text("body")
            notes.append({
                "viewport": label,
                "has_rubric": "量规" in body,
                "has_auto": "自动检查" in body,
                "has_ai": "AI 建议" in body,
                "has_teacher": "教师确认" in body,
                "snapshot_b": "Snapshot B" in body,
                "score": "95" in body,
            })
            page.screenshot(path=str(OUT / f"teacher-review-{label}.png"), full_page=False)
            print("wrote", label, notes[-1])
            page.close()
        browser.close()
    (OUT / "teacher-qa-notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
