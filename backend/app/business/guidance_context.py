"""Load bounded learning context at the model boundary, outside checkpoints."""
import hashlib
import json
from dataclasses import replace

from sqlalchemy import select

from app.teaching_control.protocols import GuidanceRequest

from .models import RequirementResult, Snapshot, TeachingEvent
from .python_basics import task_pack_for


class ContextualTeachingLLM:
    def __init__(self, *, delegate, factory, manager, bridge):
        self.delegate, self.factory, self.manager, self.bridge = delegate, factory, manager, bridge

    async def enrich(self, request: GuidanceRequest) -> GuidanceRequest:
        async with self.factory() as session:
            snapshot = await session.get(Snapshot, request.snapshot_id) if request.snapshot_id else None
            if snapshot is None or snapshot.attempt_id != request.attempt_id:
                return request
            events = list((await session.scalars(select(TeachingEvent).where(
                TeachingEvent.attempt_id == request.attempt_id,
            ).order_by(TeachingEvent.created_at.desc()).limit(30))).all())
            history = []
            for event in reversed(events):
                payload = event.payload or {}
                if payload.get("stage") != request.stage:
                    continue
                guidance = payload.get("guidance") or {}
                if guidance.get("success") is not True:
                    guidance = {}
                observation = str(payload.get("student_observation") or "")[:1200]
                if guidance or observation:
                    history.append({"observation": observation,
                                    "guidance": str(guidance.get("message") or "")[:800],
                                    "next_step": str(guidance.get("next_step") or "")[:300],
                                    "snapshot_id": str(payload.get("snapshot_id") or "")})
            results = list((await session.scalars(select(RequirementResult).where(
                RequirementResult.attempt_id == request.attempt_id,
                RequirementResult.snapshot_id == snapshot.id,
            ).order_by(RequirementResult.evaluated_at.desc()).limit(8))).all())
        root = self.manager.snapshot_directory(request.attempt_id, snapshot.id).resolve()
        sources = []
        files = ("main.py", "README.md") if task_pack_for(request.task_version) else ("faq_app.py", "README.md")
        for name in files:
            path = root / name
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
                with path.open(encoding="utf-8", errors="replace") as stream:
                    sources.append(f"{name}\n{stream.read(6000)}")
        facts = []
        for result in results:
            digest = hashlib.sha256(result.operation_id.encode()).hexdigest()
            path = self.manager.evidence_directory(request.attempt_id) / f"{digest}.artifact.json"
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 256_000:
                try:
                    item = json.loads(path.read_text(encoding="utf-8"))
                    facts.append({"status": result.status, "code": item.get("code"),
                                  "stdout": str(item.get("stdout") or "")[:1600],
                                  "stderr": str(item.get("stderr") or "")[:1000]})
                except (OSError, ValueError):
                    continue
        return replace(request, learning_history=tuple(history[-4:]),
                       source_context="\n\n".join(sources), execution_details=tuple(facts))

    def generate_guidance(self, request: GuidanceRequest):
        enriched = self.bridge.call(self.enrich(request))
        return self.delegate.generate_guidance(enriched)
