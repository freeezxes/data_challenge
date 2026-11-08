"""Regression coverage for boundary cases beyond the independent QA scenarios."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest

from app.credentials import request_key
from app.server import App
from app.store import Store, StoreError
from cqa.checklist import validate_checklist, normalize_answer
from cqa.derive import kind_of
from scripts.qa_platform import platform, seed_result, wait_job


def test_installed_sdk_accepts_request_key_and_ignores_process_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'server-key-must-not-be-used')
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'server-token-must-not-be-used')
    app = App(tmp_path / 'sdk.db')
    key = 'sk-ant-local-test-' + 'x' * 32
    token = request_key.set(key)
    try:
        client = app.client()  # Real SDK constructor; no network or model call.
        assert client.api_key == key
        assert not client.auth_token
        client.close()
    finally:
        request_key.reset(token)
        app.pool.shutdown()
        app.store.db.close()


def test_error_trace_does_not_become_an_evaluated_result():
    with platform() as (app, model, req):
        model.stop_reason = 'refusal'
        _, job = req('/api/jobs', {'dataset_id': 'QA', 'votes': 1})
        wait_job(app, job['id'])
        for path in ('/api/state', '/api/datasets/QA/conversations', '/api/conversations/QA/qa-chat',
                     '/api/operators?ds=QA', '/api/analytics/QA', '/api/datasets/QA/export?format=json'):
            assert req(path)[0] == 200, path
        ev = app.store.latest('QA')['qa-chat']
        assert ev['result']['usage']['output_tokens'] > 0 and 'final' not in ev
        assert req('/api/appeals', {'evaluation_id': ev['id'], 'criterion_id': 'c01', 'comment': 'test'})[0] == 400
        assert req('/api/analytics/QA')[1]['n_evaluated'] == 0
        assert req('/api/analytics/QA')[1]['cost_usd'] > 0


def test_failed_variant_start_rolls_back_new_transcript():
    with platform() as (app, _, req):
        eid = seed_result(app)
        def fail_client():
            raise RuntimeError('SDK construction failed')
        app.client_factory = fail_client
        code, _ = req(f'/api/evaluations/{eid}/variant', {'target_id': 'm4', 'text': 'New operator reply', 'votes': 1})
        assert code == 500
        assert not app.store.records('SANDBOX') and not app.store.jobs()


def test_reruns_cost_includes_all_attempts_once():
    with platform() as (app, model, req):
        for _ in range(2):
            _, job = req('/api/jobs', {'dataset_id': 'QA', 'votes': 1})
            wait_job(app, job['id'])
        seed_result(app)  # Imported result with no job still has a reported cost.
        expected = sum(j['cost_usd'] for j in app.store.jobs()) + .01
        assert req('/api/analytics/QA')[1]['cost_usd'] == pytest.approx(expected)


def test_appeal_uniqueness_across_store_connections():
    with platform(False) as (app, _, _):
        eid = seed_result(app)
        second = Store(app.store.path)
        gate = threading.Barrier(2)
        def appeal(store):
            gate.wait()
            try:
                return store.create_appeal(eid, 'c01', 'Parallel review')
            except StoreError:
                return None
        try:
            with ThreadPoolExecutor(2) as pool:
                ids = list(pool.map(appeal, [app.store, second]))
            assert sum(i is not None for i in ids) == 1
            assert len(app.store.appeals('open')) == 1
        finally:
            second.db.close()


def test_custom_scales_use_their_own_answers(checklist):
    for cid in ('c04', 'c06'):
        criterion = deepcopy(next(c for c in checklist['criteria'] if c['id'] == cid))
        criterion['scale'] = [{'value': n, 'text': str(n)} for n in (0, 50, 100)]
        assert kind_of(criterion) is None
        assert normalize_answer(criterion, '50') == 50
        for invalid in (50.9, True, float('nan'), float('inf'), {}, []):
            with pytest.raises((ValueError, TypeError)):
                normalize_answer(criterion, invalid)


def test_invalid_checklist_shapes_rejected_without_exception(checklist):
    for criteria in (None, {}, [None], [{'id': []}], [{'id': 'x', 'weight': '10'}]):
        body = deepcopy(checklist)
        body['criteria'] = criteria
        assert validate_checklist(body)
    body = deepcopy(checklist)
    next(c for c in body['criteria'] if c['type'] == 'scale')['scale'].append(None)
    assert validate_checklist(body)
    for weight in (float('nan'), float('inf'), True, -1):
        body = deepcopy(checklist)
        body['criteria'][0]['weight'] = weight
        assert validate_checklist(body)
