"""Adapt Claude's original labels for the viewer without inventing explanations."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cqa.gold import load_gold
from cqa.scoring import score

SOURCE_COMMIT = '98e87b7984a6ba9ddd34cea241c0d5fb5e7e8810'


def build():
    source = ROOT / 'data/gold/dev_gold.json'
    original = json.loads(source.read_text())
    gold = load_gold(source)
    config = json.loads((ROOT / 'config/checklist.default.json').read_text())
    dataset_path = ROOT / 'public/dataset.json'
    dataset = json.loads(dataset_path.read_text())['data']['records']
    chatgpt = json.loads((ROOT / 'public/annotations.json').read_text())
    criteria = copy.deepcopy(chatgpt['criteria'])
    assert set(gold['items']) == {r['conversation_id'] for r in dataset}, 'Dataset ID mismatch'
    assert len(gold['items']) == len(original['items']), 'Duplicate source IDs'
    assert len(criteria) == len(config['criteria']) == 8
    for criterion, raw in zip(criteria, config['criteria']):
        criterion['weight'] = raw['weight']
        rule = raw.get('yes_when') or '; '.join(f"{s['value']} — {s['text']}" for s in raw.get('scale', []))
        if criterion['id'] == 'closing':
            rule += ' В этой версии также засчитывается вежливая формула вместе с прощанием.'
        if raw.get('na_when'):
            rule += ' Не применяется: ' + raw['na_when']
        criterion['rule'] = rule
    annotations = []
    for record in dataset:
        cid = record['conversation_id']
        item = gold['items'][cid]
        result = score(config, item['answers'])
        verdicts = []
        for criterion, row in zip(criteria, result['criteria']):
            answer = {'yes': 'Да', 'no': 'Нет', 'na': 'N/A'}.get(row['answer'], row['answer'])
            ambiguous = row['id'] in item['ambiguous']
            verdicts.append({
                'criterion_id': criterion['id'], 'value': answer, 'raw_points': row['raw_points'],
                'rationale': 'В исходной разметке Claude указана оценка; отдельного обоснования для этого критерия нет.',
                'evidence': [], 'inspected_range': None, 'review_required': ambiguous,
                'review_note': 'Claude пометил этот критерий как спорный.' if ambiguous else None,
            })
        weighted = [r for r in result['criteria'] if r['raw_points'] is not None]
        annotations.append({
            'conversation_id': cid, 'status': 'annotated', 'author': 'claude',
            'summary': next((m['text'] for m in record['transcript'] if m['speaker'] == 'client' and len(m['text']) > 12), cid),
            'transcript_sha256': hashlib.sha256(json.dumps(record['transcript'], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            'criteria': verdicts, 'applicable_weight': sum(r['weight'] for r in weighted),
            'earned_weighted_points': sum(r['raw_points'] for r in weighted), 'total_score': result['total'],
            'critical_flag': result['critical'], 'review_required': bool(item['ambiguous']),
            'channel_scope': 'transcript_only' if record['channel'] == 'voice call' else 'chat_transcript',
            'notes': [item['note']] if item['note'] else [], 'closing_type': item['closing'],
        })
    output = {
        'schema_version': '1.0', 'annotation_version': 'claude-draft-v1',
        'provenance': {'author': 'claude', 'attribution': 'Attributed to Claude by the user',
                       'official_ground_truth': False, 'human_reviewed': False,
                       'source_commit': SOURCE_COMMIT, 'source_path': 'data/gold/dev_gold.json',
                       'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                       'source_dataset_sha256': hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                       'dataset_alignment': 'conversation_id', 'closing_policy': gold['policy']},
        'policy_notes': [
            'Разметка Claude из GitHub. Это рабочая версия команды, а не эталон организаторов.',
            'Показаны исходные оценки Claude. Итоги рассчитаны по весам и правилам исключения неприменимых критериев.',
            'Завершение: засчитываются итог, предложение помощи или вежливая формула вместе с прощанием; одно прощание не засчитывается.',
            'В исходном файле есть общие заметки и спорные критерии, но нет отдельных цитат-доказательств. Обоснования ChatGPT сюда не переносились.',
            'Неприменимый критерий исключается из расчёта; остальные веса пересчитываются до 100. Грубые нарушения отмечаются отдельным флагом.',
        ],
        'criteria': criteria, 'counts': {'annotated': len(annotations), 'dataset': len(dataset),
                                        'review_required': sum(a['review_required'] for a in annotations),
                                        'critical': sum(a['critical_flag'] for a in annotations)},
        'annotations': annotations,
    }
    (ROOT / 'public/annotations-claude.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(output['counts']))
    return output


if __name__ == '__main__':
    build()
