"""Stage 05B submission, teacher review, and formal grade rules."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Attempt,
    FormalGrade,
    OperationLedger,
    RequirementDefinition,
    RequirementResult,
    Review,
    ReviewItem,
    RubricDefinition,
    Snapshot,
    Submission,
    TaskStage,
    TeachingEvent,
    User,
)
from .service import BusinessRuleError, BusinessService

REQUIREMENT_TITLES = {
    "explain_scope": "说明任务边界",
    "identify_citation_rule": "识别引用规则",
    "source_manifest": "资料正常加载",
    "source_quality_review": "资料质量复核",
    "retrieval_public_tests": "已知问题能够命中",
    "retrieval_observation": "已提交调试观察",
    "answer_public_tests": "回答符合基础测试",
    "citation_static_check": "回答包含来源引用",
    "unknown_question_test": "未知问题不伪造命中",
    "boundary_transfer": "完成边界迁移任务",
    "delivery_static_check": "交付文件完整",
    "delivery_review": "教师完成交付复核",
}


def _check_explanation(*, auto_status: str, title: str) -> str:
    if auto_status == "SATISFIED":
        return f"「{title}」相关自动证据已满足。建议教师核对学生解释与证据是否一致后再确认，检查说明不代表正式成绩。"
    if auto_status == "NOT_SATISFIED":
        return f"「{title}」在当前提交 Snapshot 上尚未满足。请依据失败证据评分；未填项保持待评价。"
    if auto_status == "INFRASTRUCTURE_ERROR":
        return f"「{title}」出现环境异常，不计入学生能力。请待证据恢复后再评价。"
    return f"「{title}」尚无自动证据。保持待评价，不要用 0 或满分占位。"


def _auto_status(results: list[RequirementResult]) -> str:
    if not results:
        return "NOT_RUN"
    if any(item.status == "INFRASTRUCTURE_ERROR" for item in results):
        return "INFRASTRUCTURE_ERROR"
    if all(item.status == "SATISFIED" for item in results):
        return "SATISFIED"
    if any(item.status == "NOT_SATISFIED" for item in results):
        return "NOT_SATISFIED"
    return "NOT_RUN"


class ReviewService:
    def __init__(self, business: BusinessService | None = None):
        self.business = business or BusinessService()

    async def assistance_history(self, session: AsyncSession, *, attempt_id: str) -> list[dict]:
        events = list((await session.scalars(
            select(TeachingEvent)
            .where(TeachingEvent.attempt_id == attempt_id)
            .order_by(TeachingEvent.created_at)
        )).all())
        assistance = []
        for event in events:
            guidance = (event.payload or {}).get("guidance")
            if not isinstance(guidance, dict) or guidance.get("success") is not True:
                continue
            assistance.append({
                "time": event.created_at.isoformat(),
                "level": guidance.get("level"),
                "message": str(guidance.get("message") or "")[:800],
                "next_step": str(guidance.get("next_step") or "")[:300],
                "success": guidance.get("success"),
                "fallback_reason": guidance.get("fallback_reason"),
            })
        return assistance

    async def latest_submission(self, session: AsyncSession, *, attempt_id: str) -> Submission | None:
        return await session.scalar(
            select(Submission)
            .where(Submission.attempt_id == attempt_id)
            .order_by(Submission.sequence.desc())
            .limit(1)
        )

    async def create_submission(
        self,
        session: AsyncSession,
        *,
        attempt: Attempt,
        user: User,
        snapshot_id: str,
        operation_id: str,
        explanation: str,
    ) -> tuple[Submission, bool]:
        if attempt.learner_id != user.id:
            raise BusinessRuleError("student_submit_required")
        existing_op = await session.scalar(
            select(Submission).where(
                Submission.attempt_id == attempt.id,
                Submission.operation_id == operation_id,
            )
        )
        if existing_op is not None:
            return existing_op, True
        existing_snapshot = await session.scalar(
            select(Submission).where(
                Submission.attempt_id == attempt.id,
                Submission.snapshot_id == snapshot_id,
            )
        )
        if existing_snapshot is not None:
            return existing_snapshot, True
        snapshot = await session.get(Snapshot, snapshot_id)
        if snapshot is None or snapshot.attempt_id != attempt.id:
            raise BusinessRuleError("snapshot_attempt_mismatch")
        latest = await session.scalar(
            select(Snapshot)
            .where(Snapshot.attempt_id == attempt.id)
            .order_by(Snapshot.sequence.desc())
            .limit(1)
        )
        if latest is None or latest.id != snapshot_id:
            raise BusinessRuleError("stale_snapshot")
        sequence = int(
            await session.scalar(
                select(Submission.sequence)
                .where(Submission.attempt_id == attempt.id)
                .order_by(Submission.sequence.desc())
                .limit(1)
            )
            or 0
        ) + 1
        assistance = await self.assistance_history(session, attempt_id=attempt.id)
        submission = Submission(
            attempt_id=attempt.id,
            snapshot_id=snapshot.id,
            operation_id=operation_id,
            sequence=sequence,
            status="submitted",
            explanation=explanation.strip(),
            assistance=assistance,
        )
        session.add(submission)
        await session.flush()
        session.add(OperationLedger(
            scope=f"submission:{attempt.id}",
            operation_id=operation_id,
            status="COMPLETED",
            result_ref=f"submission:{submission.id}",
            result_payload={"snapshot_id": snapshot.id, "sequence": sequence},
        ))
        await session.commit()
        await session.refresh(submission)
        return submission, False

    async def evaluation_view(
        self,
        session: AsyncSession,
        *,
        submission: Submission,
        teacher: User | None,
    ) -> dict:
        attempt = await session.get(Attempt, submission.attempt_id)
        snapshot = await session.get(Snapshot, submission.snapshot_id)
        if attempt is None or snapshot is None:
            raise BusinessRuleError("submission_context_incomplete")
        learner = await session.get(User, attempt.learner_id)
        stage = await session.get(TaskStage, attempt.current_stage_id)
        rubric = await self.business.ensure_rubric(session, task_version_id=attempt.task_version_id)
        await session.commit()
        definitions = list((await session.scalars(
            select(RequirementDefinition)
            .join(TaskStage, RequirementDefinition.task_stage_id == TaskStage.id)
            .where(TaskStage.task_version_id == attempt.task_version_id)
        )).all())
        by_key = {item.requirement_key: item for item in definitions if item.requirement_key}
        results = list((await session.scalars(
            select(RequirementResult).where(
                RequirementResult.attempt_id == attempt.id,
                RequirementResult.snapshot_id == snapshot.id,
            ).order_by(RequirementResult.evaluated_at, RequirementResult.id)
        )).all())
        results_by_req = {}
        for item in results:
            results_by_req[item.requirement_id] = item
        review = await session.scalar(select(Review).where(Review.submission_id == submission.id))
        if review is None and teacher is not None:
            review = Review(submission_id=submission.id, teacher_id=teacher.id, status="draft")
            session.add(review)
            await session.flush()
        items_by_key = {}
        if review is not None:
            for item in (await session.scalars(select(ReviewItem).where(ReviewItem.review_id == review.id))).all():
                items_by_key[item.rubric_key] = item
        rubric_view = []
        missing = []
        for definition in rubric:
            related = []
            for key in definition.requirement_keys or []:
                req = by_key.get(key)
                result = results_by_req.get(req.id) if req else None
                related.append({
                    "key": key,
                    "name": REQUIREMENT_TITLES.get(key, key),
                    "kind": req.kind if req else "",
                    "status": result.status if result else "NOT_RUN",
                    "operation_id": result.operation_id if result else None,
                    "evaluator": result.evaluator if result else (req.evaluator if req else ""),
                })
            auto_status = _auto_status([
                results_by_req[by_key[key].id]
                for key in definition.requirement_keys or []
                if key in by_key and by_key[key].id in results_by_req
            ])
            stored = items_by_key.get(definition.item_key)
            auto_summary = stored.auto_summary if stored else (
                "相关自动检查均已满足。" if auto_status == "SATISFIED"
                else "相关自动检查尚未全部满足。" if auto_status == "NOT_SATISFIED"
                else "环境异常，不计入学生能力。" if auto_status == "INFRASTRUCTURE_ERROR"
                else "尚无绑定该提交 Snapshot 的自动证据。"
            )
            ai_text = stored.ai_text if stored else _check_explanation(auto_status=auto_status, title=definition.title)
            evidence_refs = stored.ai_evidence_refs if stored else [
                item["operation_id"] for item in related if item["operation_id"]
            ]
            if stored is None and review is not None:
                stored = ReviewItem(
                    review_id=review.id,
                    rubric_key=definition.item_key,
                    auto_status=auto_status,
                    auto_summary=auto_summary,
                    ai_text=ai_text,
                    ai_evidence_refs=evidence_refs,
                )
                session.add(stored)
                await session.flush()
            confirmed = bool(stored and stored.teacher_confirmed)
            score = stored.teacher_score if stored else None
            if not confirmed or score is None:
                missing.append(definition.item_key)
            rubric_view.append({
                "key": definition.item_key,
                "title": definition.title,
                "max_score": definition.max_score,
                "requirements": related,
                "auto": {"status": stored.auto_status if stored else auto_status, "summary": stored.auto_summary if stored else auto_summary},
                "ai": {"text": stored.ai_text if stored else ai_text, "evidence_refs": stored.ai_evidence_refs if stored else evidence_refs, "score": None},
                "teacher": {
                    "score": stored.teacher_score if stored else None,
                    "reason": stored.teacher_reason if stored else "",
                    "confirmed": confirmed,
                    "status": "confirmed" if confirmed and score is not None else "pending",
                },
            })
        if review is not None:
            await session.commit()
        grade = await session.scalar(select(FormalGrade).where(FormalGrade.submission_id == submission.id))
        publisher = await session.get(User, grade.published_by) if grade else None
        variant = next((item for row in rubric_view for item in row["requirements"] if item["key"] == "boundary_transfer"), None)
        return {
            "submission": {
                "id": submission.id,
                "sequence": submission.sequence,
                "status": submission.status,
                "snapshot_id": snapshot.id,
                "snapshot_label": f"Snapshot {chr(64 + snapshot.sequence)}" if 0 < snapshot.sequence <= 26 else f"Snapshot {snapshot.sequence}",
                "created_at": (submission.created_at or datetime.now(UTC)).isoformat(),
                "explanation": submission.explanation,
                "assistance": submission.assistance,
            },
            "attempt": {
                "id": attempt.id,
                "state_version": attempt.state_version,
                "status": attempt.status,
                "stage": stage.title if stage else "",
            },
            "student": {"id": learner.id if learner else "", "display_name": learner.display_name if learner else "未知学生"},
            "variant": variant or {"key": "boundary_transfer", "name": "完成边界迁移任务", "status": "NOT_RUN"},
            "rubric": rubric_view,
            "review": {
                "id": review.id if review else None,
                "status": review.status if review else "draft",
                "can_publish": not missing and review is not None and review.status != "published",
                "missing": missing,
                "published_at": review.published_at.isoformat() if review and review.published_at else None,
            },
            "formal_grade": None if grade is None else {
                "total_score": grade.total_score,
                "max_score": grade.max_score,
                "published_at": grade.published_at.isoformat(),
                "published_by": publisher.display_name if publisher else "",
                "change_reason": grade.change_reason,
            },
        }

    async def rubric_limits(self, session: AsyncSession, review: Review) -> dict[str, int]:
        submission = await session.get(Submission, review.submission_id)
        attempt = await session.get(Attempt, submission.attempt_id)
        definitions = list((await session.scalars(select(RubricDefinition).where(
            RubricDefinition.task_version_id == attempt.task_version_id,
        ))).all())
        return {item.item_key: item.max_score for item in definitions}

    async def save_draft(
        self,
        session: AsyncSession,
        *,
        review: Review,
        teacher: User,
        items: list[dict],
    ) -> Review:
        if review.status == "published":
            raise BusinessRuleError("review_already_published")
        review.teacher_id = teacher.id
        stored = {
            item.rubric_key: item
            for item in (await session.scalars(select(ReviewItem).where(ReviewItem.review_id == review.id))).all()
        }
        max_scores = await self.rubric_limits(session, review)
        allowed = set(max_scores)
        for payload in items:
            key = str(payload.get("key") or "")
            if key not in allowed or key not in stored:
                raise BusinessRuleError("unknown_rubric_item")
            item = stored[key]
            raw_score = payload.get("score")
            confirmed = bool(payload.get("confirmed"))
            reason = str(payload.get("reason") or "").strip()
            if "formal_grade" in payload or "total_score" in payload:
                raise BusinessRuleError("formal_grade_not_writable")
            if raw_score is None or raw_score == "":
                item.teacher_score = None
                item.teacher_confirmed = False
            else:
                score = int(raw_score)
                if score < 0 or score > max_scores[key]:
                    raise BusinessRuleError("score_out_of_range")
                item.teacher_score = score
                item.teacher_confirmed = confirmed and True
            item.teacher_reason = reason
            if item.teacher_confirmed and item.teacher_score is None:
                item.teacher_confirmed = False
        await session.commit()
        await session.refresh(review)
        return review

    async def publish(
        self,
        session: AsyncSession,
        *,
        review: Review,
        teacher: User,
        operation_id: str,
        change_reason: str = "",
    ) -> FormalGrade:
        existing = await session.scalar(
            select(OperationLedger).where(
                OperationLedger.scope == f"review-publish:{review.id}",
                OperationLedger.operation_id == operation_id,
            )
        )
        if existing is not None:
            grade = await session.get(FormalGrade, str(existing.result_ref or "").removeprefix("formal-grade:"))
            if grade is None:
                raise BusinessRuleError("grade_ledger_corrupt")
            return grade
        if review.status == "published":
            grade = await session.scalar(select(FormalGrade).where(FormalGrade.review_id == review.id))
            if grade is None:
                raise BusinessRuleError("published_review_missing_grade")
            return grade
        items = list((await session.scalars(select(ReviewItem).where(ReviewItem.review_id == review.id))).all())
        max_scores = await self.rubric_limits(session, review)
        if not max_scores or {item.rubric_key for item in items} != set(max_scores):
            raise BusinessRuleError("rubric_incomplete")
        total = 0
        max_total = 0
        for item in items:
            max_total += max_scores[item.rubric_key]
            if not item.teacher_confirmed or item.teacher_score is None:
                raise BusinessRuleError("unconfirmed_rubric_item")
            if not item.teacher_reason.strip():
                raise BusinessRuleError("teacher_reason_required")
            total += item.teacher_score
        review.status = "published"
        review.teacher_id = teacher.id
        review.publish_operation_id = operation_id
        review.published_at = datetime.now(UTC)
        grade = FormalGrade(
            submission_id=review.submission_id,
            review_id=review.id,
            total_score=total,
            max_score=max_total,
            published_by=teacher.id,
            change_reason=change_reason.strip(),
        )
        session.add(grade)
        await session.flush()
        session.add(OperationLedger(
            scope=f"review-publish:{review.id}",
            operation_id=operation_id,
            status="COMPLETED",
            result_ref=f"formal-grade:{grade.id}",
            result_payload={"total_score": total, "max_score": max_total},
        ))
        await session.commit()
        await session.refresh(grade)
        return grade

    async def record_teacher_requirement(
        self,
        session: AsyncSession,
        *,
        attempt: Attempt,
        submission: Submission,
        teacher: User,
        requirement_key: str,
        status: str,
        operation_id: str,
        reason: str,
    ) -> RequirementResult:
        if status not in {"SATISFIED", "NOT_SATISFIED"}:
            raise BusinessRuleError("invalid_review_status")
        if not reason.strip():
            raise BusinessRuleError("teacher_reason_required")
        definition = await session.scalar(
            select(RequirementDefinition)
            .join(TaskStage, RequirementDefinition.task_stage_id == TaskStage.id)
            .where(
                TaskStage.task_version_id == attempt.task_version_id,
                RequirementDefinition.requirement_key == requirement_key,
            )
        )
        if definition is None or definition.kind != "TEACHER_REVIEW":
            raise BusinessRuleError("teacher_review_required")
        ledger_scope = f"teacher-review:{submission.id}"
        ledger = await session.scalar(
            select(OperationLedger).where(
                OperationLedger.scope == ledger_scope,
                OperationLedger.operation_id == operation_id,
            )
        )
        if ledger is not None:
            result = await session.get(RequirementResult, str(ledger.result_ref or "").removeprefix("requirement-result:"))
            if result is None:
                raise BusinessRuleError("requirement_ledger_corrupt")
            return result
        result = RequirementResult(
            attempt_id=attempt.id,
            requirement_id=definition.id,
            snapshot_id=submission.snapshot_id,
            operation_id=operation_id,
            status=status,
            evaluator="teacher_review_v1",
            evidence_refs=[f"teacher://{teacher.id}", f"submission://{submission.id}"],
            version=definition.version,
            evaluated_at=datetime.now(UTC),
        )
        session.add(result)
        await session.flush()
        session.add(OperationLedger(
            scope=ledger_scope,
            operation_id=operation_id,
            status="COMPLETED",
            result_ref=f"requirement-result:{result.id}",
            result_payload={"requirement_key": requirement_key, "status": status, "reason": reason.strip()},
        ))
        await session.commit()
        await session.refresh(result)
        return result
