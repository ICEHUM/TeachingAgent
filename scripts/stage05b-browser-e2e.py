"""Real Edge E2E for student submission, frozen Snapshot B review, publish, and recap."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "stage05b-screenshots"
OUT.mkdir(parents=True, exist_ok=True)
VIEWPORTS = [("1440x900", 1440, 900), ("1366x768", 1366, 768), ("1280x800", 1280, 800), ("390x844", 390, 844)]
API = "http://127.0.0.1:8000"


def viewport_health(page: Page) -> dict:
    return page.evaluate("""() => ({
      viewport: [window.innerWidth, window.innerHeight],
      scrollWidth: document.documentElement.scrollWidth,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
      bodyText: document.body.innerText.slice(0, 5000)
    })""")


def open_teacher_review(page: Page, teacher_url: str, *, completed: bool) -> None:
    page.goto(teacher_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(".student-list", timeout=15000)
    page.wait_for_timeout(500)
    group = "已完成" if completed else "需要关注"
    group_button = page.locator("nav.group-tabs button").filter(has_text=group)
    if group_button.get_attribute("aria-current") != "true":
        group_button.click()
        page.wait_for_timeout(500)
    rows = page.locator(".student-row")
    if rows.count() == 0:
        raise AssertionError(f"no student row in {group}")
    target = rows.filter(has_text="提交待复核").first if not completed else rows.filter(has_text="成绩已发布").first
    if target.count() == 0:
        target = rows.first
    target.click()
    try:
        page.wait_for_selector(".detail-head", timeout=35000)
    except PlaywrightTimeoutError:
        page.screenshot(path=str(OUT / f"teacher-detail-failure-{group}.png"), full_page=False)
        body = page.locator("body").inner_text()
        raise AssertionError(f"teacher detail did not open for {group}: {body[-4000:]}") from None
    for name in ("评价作品", "查看评价", "打开评价页"):
        button = page.get_by_role("button", name=name)
        if button.count():
            button.first.click()
            break
    else:
        raise AssertionError("teacher review entry is missing")
    page.wait_for_selector(".eval-shell", timeout=15000)


def main() -> None:
    backend_python = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    fixture = ROOT / "scripts" / "stage05b-browser-fixture.py"
    fixture_env = os.environ.copy()
    fixture_env["PYTHONPATH"] = str(ROOT / "backend")
    fixture_env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    subprocess.run(
        [str(backend_python), str(fixture), "prepare"],
        cwd=ROOT,
        env=fixture_env,
        check=True,
        capture_output=True,
        text=True,
    )
    print("browser-e2e: fixture prepared", flush=True)
    fixture_context = ROOT / ".runtime" / "stage05b-browser-context.json"
    context = json.loads(fixture_context.read_text(encoding="utf-8"))
    student_url = f"http://127.0.0.1:5173/?user={context['student_id']}&attempt={context['attempt_id']}"
    teacher_url = f"http://127.0.0.1:5174/?user={context['teacher_id']}"
    notes: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        for label, width, height in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(student_url + "&view=submit", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".eval-shell", timeout=15000)
            page.wait_for_timeout(400)
            text = page.locator("body").inner_text()
            assert "确认提交" in text and "Snapshot B" in text
            page.screenshot(path=str(OUT / f"student-submit-{label}.png"), full_page=False)
            health = viewport_health(page)
            notes.append({"page": "student-submit", "viewport": label, **{k: v for k, v in health.items() if k != "bodyText"}})
            page.close()

        student = browser.new_page(viewport={"width": 1440, "height": 900})
        student.goto(student_url + "&view=submit", wait_until="domcontentloaded", timeout=30000)
        submit = student.get_by_role("button", name="确认提交当前 Snapshot")
        submit.wait_for(state="visible", timeout=15000)
        submit.click()
        student.get_by_text("学习复盘", exact=True).wait_for(state="visible", timeout=15000)

        with httpx.Client(base_url=API, timeout=30.0) as client:
            headers = {"X-User-Id": context["student_id"]}
            current = client.get(f"/api/product/attempts/{context['attempt_id']}/submissions/latest", headers=headers)
            current.raise_for_status()
            submission_id = current.json()["submission"]["id"]
        context["submission_id"] = submission_id
        print("browser-e2e: student submitted Snapshot B", flush=True)
        subprocess.run(
            [str(backend_python), str(fixture), "snapshot-c"],
            cwd=ROOT,
            env=fixture_env,
            check=True,
            capture_output=True,
            text=True,
        )
        print("browser-e2e: student created Snapshot C", flush=True)
        context.update(json.loads(fixture_context.read_text(encoding="utf-8")))

        teacher = browser.new_page(viewport={"width": 1440, "height": 900})
        open_teacher_review(teacher, teacher_url, completed=False)
        print("browser-e2e: teacher opened frozen submission", flush=True)
        review_text = teacher.locator("body").inner_text()
        assert "Snapshot B" in review_text and "作品评价" in review_text
        while teacher.get_by_role("button", name="确认本项").count():
            teacher.get_by_role("button", name="确认本项").first.click()
            teacher.wait_for_timeout(250)
        scores = [40, 25, 20, 10]
        cards = teacher.locator(".rubric-card")
        assert cards.count() == 4
        for index, score in enumerate(scores):
            card = cards.nth(index)
            card.locator('input[type="number"]').fill(str(score))
            card.locator("textarea").fill(f"依据提交 Snapshot B 的证据逐项确认，第 {index + 1} 项。")
            checkbox = card.locator('input[type="checkbox"]')
            checkbox.check()
        teacher.screenshot(path=str(OUT / "teacher-review-draft-1440x900.png"), full_page=False)
        publish = teacher.locator(".eval-actions button.primary")
        publish.wait_for(state="visible", timeout=10000)
        assert publish.is_enabled()
        publish.click()
        teacher.get_by_text("已发布 95/100", exact=True).wait_for(state="visible", timeout=15000)
        print("browser-e2e: teacher published 95/100", flush=True)

        with httpx.Client(base_url=API, timeout=30.0) as client:
            teacher_headers = {"X-User-Id": context["teacher_id"]}
            frozen = client.get(f"/api/product/submissions/{submission_id}", headers=teacher_headers)
            frozen.raise_for_status()
            payload = frozen.json()
            assert payload["submission"]["snapshot_id"] == context["snapshot_b"]
            assert payload["submission"]["snapshot_id"] != context["snapshot_c"]
            assert payload["formal_grade"]["total_score"] == 95
            assert all(item["ai"]["score"] is None for item in payload["rubric"])

        for label, width, height in VIEWPORTS:
            recap = browser.new_page(viewport={"width": width, "height": height})
            recap.goto(student_url + "&view=recap", wait_until="domcontentloaded", timeout=30000)
            recap.wait_for_selector(".eval-shell", timeout=15000)
            recap.wait_for_timeout(350)
            recap_text = recap.locator("body").inner_text()
            assert all(item in recap_text for item in ("自动检查", "AI 建议", "教师确认", "95/100", "Snapshot B"))
            recap.screenshot(path=str(OUT / f"student-recap-{label}.png"), full_page=False)
            health = viewport_health(recap)
            notes.append({
                "page": "student-recap", "viewport": label,
                "has_three_lanes": all(item in recap_text for item in ("自动检查", "AI 建议", "教师确认")),
                "snapshot_b": "Snapshot B" in recap_text,
                **{k: v for k, v in health.items() if k != "bodyText"},
            })
            recap.close()

            review = browser.new_page(viewport={"width": width, "height": height})
            open_teacher_review(review, teacher_url, completed=True)
            review_text = review.locator("body").inner_text()
            assert "Snapshot B" in review_text and "已发布 95/100" in review_text
            review.screenshot(path=str(OUT / f"teacher-review-{label}.png"), full_page=False)
            health = viewport_health(review)
            notes.append({
                "page": "teacher-review", "viewport": label,
                "snapshot_b": "Snapshot B" in review_text,
                "published_95": "已发布 95/100" in review_text,
                **{k: v for k, v in health.items() if k != "bodyText"},
            })
            review.close()
        student.close()
        teacher.close()
        browser.close()
    print("browser-e2e: four-view screenshots complete", flush=True)

    browser_report = {
        "attempt_id": context["attempt_id"],
        "submission_id": context["submission_id"],
        "snapshot_b": context["snapshot_b"],
        "snapshot_c": context["snapshot_c"],
        "submission_frozen_to_b": context["snapshot_b"] != context["snapshot_c"],
        "browser_actions": [
            "student_confirmed_submission",
            "student_continued_to_snapshot_c",
            "teacher_opened_review",
            "teacher_confirmed_teacher_review_requirements",
            "teacher_completed_four_rubric_items_with_reasons",
            "teacher_published_formal_grade",
            "student_opened_recap",
        ],
        "formal_grade": {"total_score": 95, "max_score": 100},
        "viewports": notes,
        "passed": all(not item.get("horizontalOverflow", False) for item in notes),
    }
    (ROOT / ".runtime" / "stage05b-context.json").write_text(json.dumps({
        "attempt_id": context["attempt_id"],
        "submission_id": context["submission_id"],
        "student_id": context["student_id"],
        "teacher_id": context["teacher_id"],
        "snapshot_b": context["snapshot_b"],
        "snapshot_c": context["snapshot_c"],
    }, indent=2), encoding="utf-8")
    (ROOT / "reports" / "stage05b-browser-e2e.json").write_text(
        json.dumps(browser_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(browser_report, ensure_ascii=True, indent=2))
    if not browser_report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
