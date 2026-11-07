"""Сохранение прогонов и метрики; разметка команды — рабочий ориентир, не официальный gold."""
import hashlib
import json
from pathlib import Path

from .evaluator import cost_usd
from .metrics import compare


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save_run(path, run):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def summarize(checklist, gold, conversations, results, errors):
    selected = {c['conversation_id']: c for c in conversations}
    successful = {r['conversation_id']: r for r in results}
    accepted = {k: r['answers'] for k, r in successful.items() if r.get('status') == 'ok'}
    eligible = {k: v for k, v in accepted.items() if gold and k in gold['items']}
    calls = [c for row in results + errors for c in row.get('calls', [])]
    costs = [cost_usd(c['model'], c['usage']) for c in calls]
    report = {
        'reference': 'team_draft_not_official_ground_truth',
        'n_requested': len(selected), 'n_completed': len(successful), 'n_valid': len(accepted),
        'n_needs_review': sum(r.get('status') == 'needs_review' for r in results),
        'n_errors': len(errors),
        'n_pending': len(selected.keys() - successful.keys() - {r['conversation_id'] for r in errors}),
        'valid_coverage': len(accepted) / len(selected) if selected else None,
        'n_api_responses': len(calls),
        'known_cost_usd': round(sum(c for c in costs if c is not None), 6),
        'cost_complete': not errors and not any(r.get('prior_errors') for r in results) and all(c is not None for c in costs),
        'cost_note': 'По полученным usage; транспортные ошибки могут иметь неизвестную стоимость.',
        'comparison': compare(checklist, gold, eligible) if gold else None,
        'slices': {}, 'detection': {},
    }
    for field in ('language', 'channel'):
        report['slices'][field] = {}
        for value in sorted({str(c.get(field) or 'unknown') for c in conversations}):
            ids = {k for k, c in selected.items() if str(c.get(field) or 'unknown') == value}
            predictions = {k: v for k, v in eligible.items() if k in ids}
            report['slices'][field][value] = {
                'n_requested': len(ids), 'n_valid': len(ids & accepted.keys()),
                'comparison': compare(checklist, gold, predictions) if gold else None,
            }
    if gold:
        for criterion in checklist['criteria']:
            cid = criterion['id']
            targets = ['na'] if criterion.get('na_allowed') else []
            if criterion['type'] == 'critical':
                targets.append('yes')
            for target in targets:
                # Missing/invalid outputs count as misses for known positive cases.
                pairs = [(g['answers'][cid], accepted.get(k, {}).get(cid))
                         for k, g in gold['items'].items() if k in selected and cid in g['answers']]
                tp = sum(g == target and p == target for g, p in pairs)
                fp = sum(g != target and p == target for g, p in pairs)
                fn = sum(g == target and p != target for g, p in pairs)
                report['detection'][f'{cid}:{target}'] = {
                    'tp': tp, 'fp': fp, 'fn': fn,
                    'precision': tp / (tp + fp) if tp + fp else None,
                    'recall': tp / (tp + fn) if tp + fn else None,
                }
    return report
