"""Compare the preserved assistant labels with incoming DEV draft labels."""
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OURS = HERE / 'ours-fe8d2423ea82.json'
INCOMING = HERE / 'incoming-98e87b7984a6-gold.json'
POLICIES = {
    'lenient': dict(summary='Да', offer='Да', courtesy='Да', bare='Нет', no_bye='Нет', cut_off='N/A'),
    'strict': dict(summary='Да', offer='Да', courtesy='Нет', bare='Нет', no_bye='Нет', cut_off='N/A'),
}

def total(values, criteria):
    points = weight = 0
    for c in criteria:
        value = values[c['id']]
        if value == 'N/A' or not c['weight']:
            continue
        weight += c['weight']
        points += (value / c['max'] if c['kind'] == 'scale' else int(value == 'Да')) * c['weight']
    return round(100 * points / weight + 1e-10, 2) if weight else None

def compare():
    ours_doc = json.loads(OURS.read_text())
    new_doc = json.loads(INCOMING.read_text())
    criteria = ours_doc['criteria']
    ours = {a['conversation_id']: a for a in ours_doc['annotations']}
    incoming = {a['conversation_id']: a for a in new_doc['items']}
    assert len(incoming) == len(new_doc['items']), 'Duplicate incoming IDs'
    common = sorted(ours.keys() & incoming.keys())
    result = {'source_commit': '98e87b7984a6ba9ddd34cea241c0d5fb5e7e8810',
              'ours_path': str(OURS.relative_to(ROOT)), 'incoming_path': str(INCOMING.relative_to(ROOT)),
              'common': len(common), 'ours_only': sorted(ours.keys() - incoming.keys()),
              'incoming_only': sorted(incoming.keys() - ours.keys()), 'policies': {}}
    for policy, closing in POLICIES.items():
        differences = []
        records = []
        transitions = {c['id']: Counter() for c in criteria}
        for cid in common:
            old = {v['criterion_id']: v['value'] for v in ours[cid]['criteria']}
            new = {}
            labels = incoming[cid]['labels']
            for i, c in enumerate(criteria, 1):
                value = closing[labels['c07_closing']] if i == 7 else labels[f'c{i:02}']
                new[c['id']] = {'yes': 'Да', 'no': 'Нет', 'na': 'N/A'}.get(value, value)
            changed = []
            for c in criteria:
                k = c['id']
                if old[k] != new[k]:
                    changed.append(k)
                    transitions[k][(str(old[k]), str(new[k]))] += 1
                    differences.append({'conversation_id': cid, 'criterion': k, 'ours': old[k], 'incoming': new[k],
                                        'ours_rationale': next(v['rationale'] for v in ours[cid]['criteria'] if v['criterion_id'] == k),
                                        'incoming_note': incoming[cid]['note'], 'incoming_ambiguous': f'c{criteria.index(c)+1:02}' in incoming[cid]['ambiguous']})
            a, b = total(old, criteria), total(new, criteria)
            assert a == ours[cid]['total_score']
            records.append({'conversation_id': cid, 'ours': a, 'incoming': b, 'delta': round(b-a, 2),
                            'changed_criteria': changed, 'critical_changed': old['critical'] != new['critical']})
        absolute = [abs(x['delta']) for x in records]
        result['policies'][policy] = {
            'criterion_disagreements': len(differences), 'criterion_comparisons': len(common)*len(criteria),
            'exact_agreement_percent': round(100*(1-len(differences)/(len(common)*len(criteria))), 2),
            'dialogs_with_changes': sum(bool(x['changed_criteria']) for x in records),
            'mean_absolute_score_delta': round(mean(absolute), 2), 'median_absolute_score_delta': median(absolute),
            'score_delta_ge_10': sum(x >= 10 for x in absolute), 'score_delta_ge_20': sum(x >= 20 for x in absolute),
            'ours_mean_score': round(mean(x['ours'] for x in records), 2),
            'incoming_mean_score': round(mean(x['incoming'] for x in records), 2),
            'critical_disagreements': [x['conversation_id'] for x in records if x['critical_changed']],
            'na_disagreements': sum((d['ours']=='N/A') != (d['incoming']=='N/A') for d in differences),
            'per_criterion': [{'id': c['id'], 'name': c['name'], 'count': sum(transitions[c['id']].values()),
                               'transitions': [{'ours': a, 'incoming': b, 'count': n} for (a,b),n in transitions[c['id']].most_common()]} for c in criteria],
            'records': sorted(records, key=lambda x: -abs(x['delta'])), 'differences': differences}
    (HERE/'comparison-98e87b7984a6.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    for policy, p in result['policies'].items():
        print(policy, json.dumps({k:v for k,v in p.items() if k not in ['differences','records']},ensure_ascii=False))
        print('largest', json.dumps(p['records'][:12],ensure_ascii=False))
    return result

if __name__ == '__main__':
    compare()
