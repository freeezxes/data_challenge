"""Сверить прогон с ожиданиями диагностического набора (contrastive.expected.json и т. п.).

    python scripts/check_expected.py data/runs/<run>.json data/calibration/contrastive.expected.json
"""
import json
import sys

run = json.load(open(sys.argv[1], encoding='utf-8'))
exp = json.load(open(sys.argv[2], encoding='utf-8'))['expected']
hits = total = 0
for r in run['results']:
    want = exp.get(r['conversation_id'], {})
    for k, v in want.items():
        if not k.startswith('c'):
            continue
        got = r['answers'].get(k)
        ok = str(got) == str(v)
        hits += ok
        total += 1
        if not ok:
            print('✗', r['conversation_id'], k, 'ожидали', v, 'модель', got, '|', r['criteria'][k]['reasoning'][:160])
print(f'{hits}/{total} целевых проверок совпали')
