"""Метаморфные тесты оценщика на живой модели.

    python scripts/run_metamorphic.py --bases p1-ru-006,p1-kz-003,p1-en-006 --model claude-opus-5-5

Не использует эталонную разметку: ожидание считается от ответа модели на исходный диалог.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cqa.checklist import load_checklist  # noqa: E402
from cqa.dataset import load_records, normalize  # noqa: E402
from cqa.evaluator import EvalConfig, Evaluator  # noqa: E402
from cqa.metamorphic import build_cases, judge  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dataset', type=Path, default=ROOT / 'public/dataset.json')
    ap.add_argument('--checklist', type=Path, default=ROOT / 'config/checklist.default.json')
    ap.add_argument('--only', help='Только эти мутации, через запятую')
    ap.add_argument('--extra-votes', type=int, default=0, help='Добор прогонов при расхождении основных')
    ap.add_argument('--bases', required=True, help='ID исходных диалогов через запятую')
    ap.add_argument('--model', default='claude-opus-5-5')
    ap.add_argument('--effort', default='high')
    ap.add_argument('--votes', type=int, default=1)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--out', type=Path)
    a = ap.parse_args()
    checklist = load_checklist(a.checklist)
    recs = {r['conversation_id']: r for r in load_records(a.dataset)}
    bases = [recs[i] for i in a.bases.split(',')]
    cases = build_cases(bases, set(a.only.split(',')) if a.only else None)
    print(f'{len(bases)} исходных диалогов, {len(cases)} мутаций, всего вызовов: {len(bases) + len(cases)} × {a.votes}')
    if a.dry_run:
        return
    ev = Evaluator(checklist, config=EvalConfig(model=a.model, effort=a.effort, votes=a.votes, use_fallback=False, extra_votes=a.extra_votes))
    jobs = [b for b in bases] + [c['record'] for c in cases]
    out = {}

    def run(rec):
        try:
            res = ev.evaluate(normalize(rec))
            return rec['conversation_id'], {'answers': res['answers'], 'cost': res['cost_usd'], 'issues': res['issues'], 'votes': res.get('votes'), 'votes_extra': res.get('votes_extra')}
        except Exception as exc:  # noqa: BLE001
            return rec['conversation_id'], {'error': repr(exc)}

    with ThreadPoolExecutor(a.workers) as pool:
        for cid, r in pool.map(run, jobs):
            out[cid] = r
            print(cid, 'ошибка' if 'error' in r else 'ok')
    verdicts = []
    for c in cases:
        b, m = out.get(c['base_id'], {}), out.get(c['record']['conversation_id'], {})
        if 'answers' not in b or 'answers' not in m:
            verdicts.append({'case': c['record']['conversation_id'], 'mutation': c['mutation'], 'status': 'error'})
            continue
        v = judge(c, b['answers'], m['answers'])
        verdicts.append({'case': c['record']['conversation_id'], 'mutation': c['mutation'], **v,
                         'got': {k: m['answers'].get(k) for k in c['expect']}})
    by = defaultdict(Counter)
    for v in verdicts:
        by[v['mutation']][v['status']] += 1
    unstable = Counter(k for v in verdicts for k in v.get('unstable', []))
    cost = round(sum(r.get('cost') or 0 for r in out.values()), 4)
    report = {'model': a.model, 'effort': a.effort, 'votes': a.votes, 'checklist_version': checklist.get('version'),
              'by_mutation': {k: dict(v) for k, v in by.items()}, 'unstable_criteria': dict(unstable),
              'cost_usd': cost, 'verdicts': verdicts, 'answers': out}
    path = a.out or ROOT / 'data/runs' / f'metamorphic-{datetime.now():%Y%m%d-%H%M%S}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('by_mutation', 'unstable_criteria', 'cost_usd')}, ensure_ascii=False, indent=1))
    for v in verdicts:
        if v['status'] == 'fail':
            print('✗', v['case'], 'ожидали', v['target'], 'получили', v['got'])
    print('Сохранено:', path)


if __name__ == '__main__':
    main()
