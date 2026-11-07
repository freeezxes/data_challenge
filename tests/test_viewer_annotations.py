"""Ensure the viewer presents two separate sources faithfully."""
import json
from pathlib import Path

from cqa.gold import load_gold
from cqa.scoring import score

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / name).read_text())


def test_chatgpt_labels_preserve_original_local_work():
    before = read('docs/comparisons/ours-fe8d2423ea82.json')
    current = read('public/annotations.json')
    assert current['annotations'] == before['annotations']
    assert not current['provenance']['official_ground_truth']


def test_claude_matches_original_github_labels_and_scores(checklist):
    source = load_gold(ROOT / 'data/gold/dev_gold.json')['items']
    viewer = read('public/annotations-claude.json')
    records = viewer['annotations']
    assert len(records) == len(source) == 65
    assert {r['conversation_id'] for r in records} == set(source)
    mapping = {'yes': 'Да', 'no': 'Нет', 'na': 'N/A'}
    for record in records:
        original = source[record['conversation_id']]
        actual = score(checklist, original['answers'])
        assert record['total_score'] == actual['total']
        assert record['critical_flag'] == actual['critical']
        for index, criterion in enumerate(record['criteria'], 1):
            value = original['answers'][f'c{index:02}']
            assert criterion['value'] == mapping.get(value, value)


def test_claude_evidence_is_not_invented_or_copied():
    source = load_gold(ROOT / 'data/gold/dev_gold.json')['items']
    dataset = {r['conversation_id']: r for r in read('public/dataset.json')['data']['records']}
    for record in read('public/annotations-claude.json')['annotations']:
        original = source[record['conversation_id']]
        assert record['notes'] == ([original['note']] if original['note'] else [])
        transcript = dataset[record['conversation_id']]['transcript']
        for c in record['criteria']:
            assert c['evidence'], (record['conversation_id'], c['criterion_id'])
            for e in c['evidence']:
                assert e['quote'] in transcript[e['message_index'] - 1]['text']


def test_real_disagreement_survives_conversion():
    by_source = [
        {r['conversation_id']: r for r in read(name)['annotations']}
        for name in ['public/annotations.json', 'public/annotations-claude.json']
    ]
    gpt, claude = by_source
    assert gpt['p1-ru-001']['total_score'] == 92.31
    assert claude['p1-ru-001']['total_score'] == 92.31
    assert gpt['p1-ru-005']['total_score'] == 70.77
    assert claude['p1-ru-005']['total_score'] == 70.77
    changed = sum(any(a['value'] != b['value'] for a, b in zip(gpt[cid]['criteria'], claude[cid]['criteria'])) for cid in gpt)
    assert changed == 11


def test_published_dataset_keeps_transcripts_and_local_audio():
    dataset = read('public/dataset.json')['data']['records']
    assert len(dataset) == 65
    assert sum(len(r['transcript']) for r in dataset) == 1268
    for record in dataset:
        if record['audio_url']:
            assert record['audio_url'].startswith('/recordings/')
            assert '?' not in record['audio_url']
            assert (ROOT / 'public' / record['audio_url'].lstrip('/')).is_file()
