---
target: 学生实训工作台 Design Iteration 1
total_score: 28
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 0
target_identity: "file:D:\\TeachingAgent\\frontend\\student\\src\\App.tsx"
target_fingerprint: "sha256:0ab49acb532ea5e3c8bd606dbffa2769b2524c4d1ec6eea0110757aeefb2a7ea"
target_path: "D:\\TeachingAgent\\frontend\\student\\src\\App.tsx"
timestamp: 2026-09-20T13-11-51Z
slug: frontend-student-src-app-tsx
---
# Design Iteration 1 — Impeccable critique

Method: dual independent review. Assessment A performed a fresh-browser design and heuristic review without detector output. Assessment B independently ran deterministic detectors and browser checks; its findings were released only after A completed.

## Design specificity

Strongly product-authored. The student stage/Snapshot/check/evidence/coach model and the teacher intervention queue are specific to an AI teaching and developer workflow. The restrained blue and neutral system is coherent and mature. Remaining generic elements are mainly implementation terms such as Workspace, Snapshot, Tool, and Reason code.

## Nielsen score

Combined score: 28/40 (Good). Visibility 3; real-world match 3; control 2; consistency 3; error prevention 3; recognition 3; efficiency 2; minimalist design 3; recovery 3; contextual help 3.

## Cognitive load

Moderate. Three of eight checks need attention: single focus, one task at a time, and progressive disclosure. The student must coordinate task, editor, validation and coach; the teacher must balance evidence and four intervention actions. Grouping is sound and no decision point exceeds four actions.

## Priority findings and resolution

- P1: Teacher evidence rows were static and did not let a teacher inspect evidence before intervening. Resolved: rows now open real evidence from the existing API, with teaching conclusion, readable explanation, technical trace, and raw output disclosure.
- P1: Student and teacher drawers lacked complete keyboard focus management. Resolved: focus enters the active panel, cycles inside at overlay breakpoints, Escape closes, and focus returns to the trigger.
- P1: Narrow-screen student toolbar overlapped validation content. Resolved with auto-sized toolbar rows and compact responsive checklist layout.
- P1: Narrow teacher cards produced duplicated or wrong labels through stale pseudo-elements. Resolved by removing generated labels and preserving explicit status text.
- P2: Teacher action dock dominated every tab. Resolved: it is a compact disclosure by default and expands only when the teacher chooses to act.
- P2: Student requirements appeared as two full checklists. Resolved: the task rail now provides a short summary while the editor footer remains authoritative.
- P2: Raw evidence preceded technical trace. Resolved: raw output is now the final, collapsed layer.

## Persona review

- New student: current stage, failed requirement and next step are visible; some English technical terms still need progressive translation.
- Power teacher: queue triage and evidence-led action are efficient; large-cohort search and keyboard accelerators remain future work.
- Keyboard and low-vision user: focus-visible styles, focus restoration and reduced-motion support are present; 200% zoom and full screen-reader traversal remain to be verified.

## Detector result

Post-fix deterministic detector: student 0 findings, teacher 0 findings. Browser console: 0 warnings and 0 errors on both pages.

## Minor observations

The real fixture contains only one waiting student, so the teacher list has intentional empty space. Some system terms remain English. Teacher long-list density and long-name behavior need a real cohort fixture.

## Questions skipped

The user requested a fixed visual-acceptance output and asked the team to wait for the next acceptance round.
