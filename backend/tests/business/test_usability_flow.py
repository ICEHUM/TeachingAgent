import hashlib
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import select
from test_student_program_runs import prepare_snapshot, setup_app

from app.business.guidance_context import ContextualTeachingLLM
from app.business.models import TaskStage, TeachingEvent
from app.teaching_control.protocols import GuidanceRequest


@pytest.mark.asyncio
async def test_help_and_teacher_feedback_are_authorized_and_reach_both_views(tmp_path):
    app, _manager, runtime, _service, attempt, teacher, student, other = await setup_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        student_headers = {'X-User-Id': student.id}
        teacher_headers = {'X-User-Id': teacher.id}
        workbench = await prepare_snapshot(client, attempt, student_headers)
        body = {'operation_id': 'help-request-1', 'expected_state_version': workbench['attempt']['state_version'], 'message': '我已核对路径和编码，仍然没有检索结果。'}
        path = f'/api/product/attempts/{attempt.id}/help'
        assert (await client.post(path, headers={'X-User-Id': other.id}, json=body)).status_code == 403
        response = await client.post(path, headers=student_headers, json=body)
        assert response.status_code == 200
        assert (await client.post(path, headers=student_headers, json=body)).json() == response.json()
        classroom = (await client.get('/api/product/teacher/classroom', headers=teacher_headers)).json()
        assert classroom['groups']['attention'][0]['reason'] == 'student_help_requested'
        feedback = dict(body, operation_id='feedback-1', expected_state_version=response.json()['state_version'], message='路径已排除，请打印问题分词与资料关键词并比较。')
        reply_path = f'/api/product/teacher/attempts/{attempt.id}/feedback'
        assert (await client.post(reply_path, headers=student_headers, json=feedback)).status_code == 403
        assert (await client.post(reply_path, headers=teacher_headers, json=feedback)).status_code == 200
        view = (await client.get(f'/api/product/attempts/{attempt.id}/workbench', headers=student_headers)).json()
        assert view['teacher_feedback']['message'] == feedback['message']
        assert view['help_requested'] is False
        assert not runtime.calls


@pytest.mark.asyncio
async def test_unchanged_snapshot_reuse_and_revision_preview(tmp_path):
    app, manager, _runtime, _service, attempt, _teacher, student, _other = await setup_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = {'X-User-Id': student.id}
        wb = await prepare_snapshot(client, attempt, headers)
        first = wb['latest_snapshot']['id']
        async with app.state.session_factory() as session:
            session.add(TeachingEvent(attempt_id=attempt.id, actor_id=student.id, operation_id='test-guidance', event_type='guidance', state_version=0,
                payload={'stage': 'implement_retrieval', 'snapshot_id': first, 'guidance': {'message': '先比较分词。', 'level': 'L1', 'success': True}}))
            await session.commit()
        preview_path = f'/api/product/attempts/{attempt.id}/submissions/latest?preview=true'
        preview = (await client.get(preview_path, headers=headers)).json()
        assert len(preview['submission']['assistance']) == 1
        submission = await client.post(f'/api/product/attempts/{attempt.id}/submissions', headers=headers,
            json={'operation_id': 'first-submit', 'snapshot_id': first, 'explanation': '第一次提交'})
        assert submission.status_code == 200
        reuse = await client.post(f'/api/product/attempts/{attempt.id}/snapshots', headers=headers,
            json={'operation_id': 'reuse-snapshot-1', 'reuse_unchanged': True})
        assert reuse.json()['id'] == first
        (manager.source_directory(attempt.id) / 'README.md').write_text('说明运行与引用', encoding='utf-8')
        changed = await client.post(f'/api/product/attempts/{attempt.id}/snapshots', headers=headers,
            json={'operation_id': 'changed-snapshot-1', 'reuse_unchanged': True})
        assert changed.json()['id'] != first
        preview = (await client.get(preview_path, headers=headers)).json()
        assert preview['submission']['id'] is None
        assert preview['submission']['snapshot_id'] == changed.json()['id']
        historical = (await client.get(preview_path.split('?')[0], headers=headers)).json()
        assert historical['submission']['snapshot_id'] == first


@pytest.mark.asyncio
async def test_model_context_contains_current_observation_code_and_bounded_history(tmp_path):
    app, manager, _runtime, _service, attempt, _teacher, student, _other = await setup_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        wb = await prepare_snapshot(client, attempt, {'X-User-Id': student.id})
    snap = wb['latest_snapshot']['id']
    async with app.state.session_factory() as session:
        session.add(TeachingEvent(attempt_id=attempt.id, actor_id=student.id, operation_id='prior-observation', event_type='guidance', state_version=0,
            payload={'stage': 'implement_retrieval', 'snapshot_id': snap, 'student_observation': '已经检查过文件路径。', 'guidance': {'message': '检查关键词匹配。'}}))
        await session.commit()
    adapter = ContextualTeachingLLM(delegate=None, factory=app.state.session_factory, manager=manager, bridge=None)
    request = GuidanceRequest(operation_id='context-test', attempt_id=attempt.id, task_version='FAQ-001-v1', stage='implement_retrieval', mode='guided_practice', level='L1', kind='question', evidence_refs=(), evidence_summary='', snapshot_id=snap, student_observation='编码也正确，关键词仍无命中。')
    enriched = await adapter.enrich(request)
    assert enriched.student_observation == request.student_observation
    assert 'def retrieve' in enriched.source_context
    assert enriched.learning_history[-1]['observation'] == '已经检查过文件路径。'
    foreign = await adapter.enrich(replace(request, attempt_id='foreign'))
    assert foreign.source_context == ''


@pytest.mark.asyncio
async def test_teacher_can_review_current_stage_without_student_submission(tmp_path):
    app, _manager, _runtime, _service, attempt, teacher, student, _other = await setup_app(tmp_path)
    async with app.state.session_factory() as session:
        from app.business.models import Attempt
        stored = await session.get(Attempt, attempt.id)
        stage = await session.scalar(select(TaskStage).where(TaskStage.task_version_id == stored.task_version_id, TaskStage.stage_key == 'prepare_sources'))
        stored.current_stage_id = stage.id
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        wb = await prepare_snapshot(client, attempt, {'X-User-Id': student.id})
        body = {'operation_id': 'stage-review-1', 'snapshot_id': wb['latest_snapshot']['id'], 'requirement_key': 'source_quality_review', 'status': 'SATISFIED', 'reason': '资料来源与任务相符，可以继续。'}
        path = f'/api/product/teacher/attempts/{attempt.id}/stage-reviews'
        assert (await client.post(path, headers={'X-User-Id': student.id}, json=body)).status_code == 403
        assert (await client.post(path, headers={'X-User-Id': teacher.id}, json=body)).status_code == 200
        view = (await client.get(f'/api/product/attempts/{attempt.id}/workbench', headers={'X-User-Id': student.id})).json()
        assert next(r for r in view['requirements'] if r['key'] == 'source_quality_review')['status'] == 'SATISFIED'
        assert '资料来源' in view['teacher_feedback']['message']


@pytest.mark.asyncio
async def test_crlf_editor_save_returns_hash_accepted_by_snapshot(tmp_path):
    app, manager, _runtime, _service, attempt, _teacher, student, _other = await setup_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = {'X-User-Id': student.id}
        path = f'/api/product/attempts/{attempt.id}'
        current = (await client.get(path + '/files/faq_app.py', headers=headers)).json()
        edited = 'def retrieve(question, sources):\r\n    return []\r\n# edit\r\n'
        saved = await client.put(path + '/files/faq_app.py', headers=headers,
            json={'content': edited, 'expected_hash': current['hash']})
        assert saved.status_code == 200
        expected = edited.replace('\r\n', '\n')
        assert saved.json()['hash'] == hashlib.sha256(expected.encode()).hexdigest()
        snapshot = await client.post(path + '/snapshots', headers=headers,
            json={'operation_id': 'crlf-snapshot', 'expected_file_hash': saved.json()['hash']})
        assert snapshot.status_code == 200
        assert (manager.source_directory(attempt.id) / 'faq_app.py').read_bytes() == expected.encode()


@pytest.mark.asyncio
async def test_custom_task_rubric_can_be_saved_and_published(tmp_path):
    from app.business.models import RubricDefinition
    app, _manager, _runtime, _service, attempt, teacher, student, _other = await setup_app(tmp_path)
    async with app.state.session_factory() as session:
        definitions = list((await session.scalars(select(RubricDefinition).where(
            RubricDefinition.task_version_id == attempt.task_version_id))).all())
        for index, definition in enumerate(definitions):
            definition.item_key = f'custom-{index}'
            definition.max_score = 10
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = {'X-User-Id': student.id}
        teacher_headers = {'X-User-Id': teacher.id}
        wb = await prepare_snapshot(client, attempt, headers)
        submission = (await client.post(f'/api/product/attempts/{attempt.id}/submissions', headers=headers,
            json={'operation_id': 'custom-submit', 'snapshot_id': wb['latest_snapshot']['id'], 'explanation': '任务评分验证'})).json()
        view = (await client.get(f"/api/product/submissions/{submission['id']}", headers=teacher_headers)).json()
        path = f"/api/product/teacher/reviews/{view['review']['id']}"
        items = [{'key': item['key'], 'score': 7, 'reason': '根据当前任务评分项核对证据。', 'confirmed': True} for item in view['rubric']]
        saved = await client.put(path, headers=teacher_headers, json={'items': items})
        assert saved.status_code == 200, saved.text
        published = await client.post(path + '/publish', headers=teacher_headers, json={'operation_id': 'custom-publish'})
        assert published.status_code == 200, published.text
        assert published.json()['formal_grade']['total_score'] == len(items) * 7
        assert published.json()['formal_grade']['max_score'] == len(items) * 10


def test_all_executable_stage_requirements_have_authorized_tools():
    from app.agent.tools import tool_catalog_for
    from app.business.faq import DEFAULT_TASK_POLICY, FAQ_STAGES
    from app.business.stage03 import REQUIREMENT_TOOL
    from app.business.stage05_api import STAGE_CHECK_TOOLS
    for stage_key, _title, requirements in FAQ_STAGES:
        for key, kind, _evaluator, _config in requirements:
            if kind in {'TEACHER_REVIEW', 'STUDENT_EXPLANATION'}:
                continue
            tool = REQUIREMENT_TOOL[key]
            assert tool in STAGE_CHECK_TOOLS[stage_key]
            assert tool in DEFAULT_TASK_POLICY['allowed_tools']
            assert tool in tool_catalog_for('FAQ-001-v1')
