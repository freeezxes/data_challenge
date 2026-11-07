"""Сравнить зафиксированные версии c02 на случайных восьмёрках и новых ситуациях."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text())


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def metric(run, expected):
    rows = {r['conversation_id']: r for r in run['results']}
    confusion = Counter()
    differences = []
    hits = total = other_hits = other_total = 0
    for cid, target in expected.items():
        row = rows.get(cid)
        valid = row is not None and row['status'] == 'ok'
        for k, value in target.items():
            if k == 'reason':
                continue
            predicted = row['answers'].get(k) if valid else 'invalid_or_missing'
            correct = predicted == value
            if k == 'c02':
                total += 1
                hits += correct
                confusion[(value, predicted)] += 1
            else:
                other_total += 1
                other_hits += correct
            if not correct:
                differences.append({'id': cid, 'criterion': k, 'expected': value,
                                    'predicted': predicted,
                                    'reason': row['criteria'].get(k, {}).get('reasoning') if row else None})
    report = run['report']
    return {'c02_hits': hits, 'c02_n': total, 'c02_confusion': {f'{g}->{p}': n for (g,p),n in confusion.items()},
            'other_hits': other_hits, 'other_n': other_total,
            'n_valid': report['n_valid'], 'n_requested': report['n_requested'],
            'cost_usd': report['known_cost_usd'], 'cost_complete': report['cost_complete'],
            'mae': report['comparison']['total_mae'] if report['comparison'] else None,
            'differences': differences}


def main():
    plan = read('docs/comparisons/calibration-2-plan.json')
    for filename, expected_hash in {**plan['annotation_sha256'], **plan['checklist_sha256']}.items():
        if sha(filename) != expected_hash:
            raise SystemExit(f'Изменился зафиксированный файл: {filename}')
    for key in ['dataset', 'expected']:
        if sha(plan['new_scenarios'][key]) != plan['new_scenarios'][key + '_sha256']:
            raise SystemExit('Изменился новый диагностический набор')
    summary = {'seed': plan['seed'], 'model': plan['model'], 'annotations_unchanged': True,
               'candidate_frozen': True, 'comparisons': {}, 'limitations': plan['limitations']}
    for group, suffix in [('random-a',''), ('random-b',''), ('new',''), ('new','-repeat1'), ('regression','')]:
        runs = [read(f'data/runs/calibration-2-{group}-v{v}{suffix}.json') for v in [1,2]]
        ids = {c['conversation_id'] for c in runs[0]['conversations']}
        planned_ids = set(plan['new_scenarios']['ids'] if group == 'new' else plan['regression_ids'] if group == 'regression' else plan['sets'][group.replace('-','_')])
        if ids != planned_ids:
            raise SystemExit('Набор отличается от запланированного')
        for v, run in enumerate(runs, 1):
            if {c['conversation_id'] for c in run['conversations']} != ids:
                raise SystemExit('Несопоставимые наборы')
            if len(run['results']) + len(run['errors']) != len(ids):
                raise SystemExit(f'Прогон {group} v{v}{suffix} ещё не завершён')
            if run['meta']['config']['model'] != plan['model']:
                raise SystemExit('Неожиданная модель')
            filename = plan['baseline_checklist'] if v == 1 else plan['candidate_checklist']
            if run['checklist'] != read(filename):
                raise SystemExit('Чек-лист в прогоне отличается от зафиксированного')
        if runs[0]['reference'] != runs[1]['reference']:
            raise SystemExit('Разная разметка сравнения')
        expected = (read(plan['new_scenarios']['expected'])['expected'] if group == 'new'
                    else {cid: runs[0]['reference']['items'][cid]['answers'] for cid in ids})
        metrics = [metric(run, expected) for run in runs]
        summary['comparisons'][group + suffix] = {'before': metrics[0], 'after': metrics[1],
                'target_distribution': dict(Counter(v['c02'] for v in expected.values()))}
    summary['total_cost_usd'] = round(sum(m['cost_usd'] for c in summary['comparisons'].values() for k,m in c.items() if k in ['before','after']), 6)
    for group in ['random-a', 'random-b', 'new', 'regression']:
        item = summary['comparisons'][group]
        print(group, 'c02:', item['before']['c02_hits'], '->', item['after']['c02_hits'], '/',item['after']['c02_n'],
              'other:', item['before']['other_hits'],'->',item['after']['other_hits'],'/',item['after']['other_n'])
    summary['new_stability'] = {}
    for version in ['before', 'after']:
        first = summary['comparisons']['new'][version]
        second = summary['comparisons']['new-repeat1'][version]
        summary['new_stability'][version] = {'hits_across_two_runs': first['c02_hits'] + second['c02_hits'],
                                            'n': first['c02_n'] + second['c02_n']}
    summary['decision'] = 'Результат экспериментальной калибровки. Значения gold и рабочая конфигурация по умолчанию сохранены. Повторные ответы на те же восемь ситуаций не являются шестнадцатью независимыми примерами.'
    out = ROOT / 'docs/comparisons/calibration-2-results.json'
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print('New repeated:', summary['new_stability'])
    print('Cost:', summary['total_cost_usd'])
    print(out)


if __name__ == '__main__':
    main()
