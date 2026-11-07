import json
import subprocess
import sys

from cqa.dataset import normalize
from cqa.evaluator import Evaluator, EvalConfig, EvaluationError
from cqa.harness import summarize, save_run
from conftest import FakeAPI, all_answers, model_payload, ROOT
import pytest


def test_failed_calls_are_not_hidden_in_recall(checklist, chat_record):
    conv = normalize(chat_record)
    gold = {'items': {conv['conversation_id']: {'answers': all_answers(checklist, c08='yes'), 'ambiguous': []}}}
    report = summarize(checklist, gold, [conv], [], [{'conversation_id': conv['conversation_id']}])
    assert report['valid_coverage'] == 0
    assert report['detection']['c08:yes']['recall'] == 0
    assert report['detection']['c08:yes']['fn'] == 1
    assert report['n_pending'] == 0
    assert not report['cost_complete']


def test_unresolved_quotes_require_review(checklist, chat_record):
    api = FakeAPI([model_payload(checklist, all_answers(checklist), quote='выдуманная цитата')])
    res = Evaluator(checklist, client=api).evaluate(normalize(chat_record))
    assert res['status'] == 'needs_review'
    assert len(res['calls']) == 2
    assert res['cost_usd'] > 0


def test_invalid_schema_retains_paid_usage(checklist, chat_record):
    ev = Evaluator(checklist, client=FakeAPI([{'summary': 'missing criteria'}]))
    with pytest.raises(EvaluationError) as err:
        ev.evaluate(normalize(chat_record))
    assert len(err.value.calls) == 1
    assert err.value.calls[0]['usage']['output_tokens'] > 0


def test_new_criterion_works_without_reference_label(checklist, chat_record):
    checklist['criteria'][0]['id'] = 'new_contact'
    conv = normalize(chat_record)
    gold = {'items': {conv['conversation_id']: {'answers': {'old': 'yes'}, 'ambiguous': []}}}
    report = summarize(checklist, gold, [conv], [{'conversation_id': conv['conversation_id'], 'status': 'ok', 'answers': all_answers(checklist)}], [])
    assert report['comparison']['total_mae'] is None


def test_dry_run_needs_no_api_and_cannot_overwrite(tmp_path):
    out = tmp_path / 'plan.json'
    cmd = [sys.executable, str(ROOT / 'scripts/run_eval.py'), '--dry-run', '--limit', '2', '--out', str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    run = json.loads(out.read_text())
    assert len(run['conversations']) == 2
    assert run['results'] == [] and run['meta']['dry_run']
    assert run['meta']['official_ground_truth'] is False
    assert subprocess.run(cmd, capture_output=True).returncode != 0


def test_atomic_save(tmp_path):
    out = tmp_path / 'run.json'
    save_run(out, {'results': [1]})
    save_run(out, {'results': [1, 2]})
    assert json.loads(out.read_text())['results'] == [1, 2]
    assert not out.with_suffix('.json.tmp').exists()


def test_no_repair_does_not_spend_second_call(checklist, chat_record):
    api = FakeAPI([model_payload(checklist, all_answers(checklist), quote='выдуманная цитата')])
    result = Evaluator(checklist, client=api, config=EvalConfig(repair=False)).evaluate(normalize(chat_record))
    assert len(api.calls) == 1
    assert result['status'] == 'needs_review'


def test_cli_budget_disables_sdk_retries(tmp_path, monkeypatch, checklist, chat_record):
    import anthropic
    from scripts import run_eval
    source = tmp_path / 'dataset.json'
    source.write_text(json.dumps([chat_record]))
    output = tmp_path / 'run.json'
    api = FakeAPI([model_payload(checklist, all_answers(checklist, c03='yes', c07='na'))])
    api.close = lambda: None
    constructor = {}
    def client(**kwargs):
        constructor.update(kwargs)
        return api
    monkeypatch.setattr(anthropic, 'Anthropic', client)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-only-placeholder')
    monkeypatch.setattr(sys, 'argv', ['run_eval', '--dataset', str(source), '--no-compare',
                                    '--no-repair', '--api-retries', '0', '--out', str(output)])
    run_eval.main()
    assert constructor['max_retries'] == 0
    assert len(api.calls) == 1
    assert json.loads(output.read_text())['meta']['api_retries'] == 0
