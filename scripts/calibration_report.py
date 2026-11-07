"""Сводка зафиксированной калибровки; не вызывает API и не меняет разметку."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cqa.harness import fingerprint

RUNS = {
    'sonnet_tuning_before': 'sonnet-smoke',
    'sonnet_tuning_after': 'calibration-1-tuning',
    'sonnet_validation_before': 'calibration-1-validation-before',
    'sonnet_validation_after': 'calibration-1-validation-after',
    'opus_tuning_after': 'calibration-1-opus-tuning',
    'opus_validation_before': 'calibration-1-opus-validation-before',
    'opus_validation_after': 'calibration-1-opus-validation',
    'contrastive_before': 'calibration-1-contrastive-before',
    'contrastive_after': 'calibration-1-contrastive-after',
}


def main():
    expected = json.loads((ROOT / 'data/calibration/contrastive.expected.json').read_text())['expected']
    summary = {'runs': {}, 'limitations': [
        'DEV уже использовался при создании разметки и оценщика; это регрессионная проверка.',
        'По одному прогону каждой конфигурации: стохастическая вариативность не измерена.',
        'Validation подобран до получения ответов и не включает прямые языковые аналоги tuning. Полного группового разбиения 65 диалогов пока нет.',
        'В validation нет положительных примеров c08: полнота обнаружения грубых нарушений на этом срезе не измеряется.',
        'Новые парные кейсы написаны ассистентом и проверяют только указанные критерии; независимой человеческой разметки у них нет.',
    ]}
    for name, filename in RUNS.items():
        path = ROOT / 'data/runs' / (filename + '.json')
        if not path.exists():
            raise SystemExit(f'Нет прогона: {path}')
        r = json.loads(path.read_text())
        if len(r['results']) + len(r['errors']) != len(r['conversations']):
            raise SystemExit(f'Прогон не завершён: {path}')
        gold = r['reference']
        matches = total = unambiguous_hits = unambiguous_total = 0
        differences = []
        for row in r['results']:
            cid = row['conversation_id']
            target = gold['items'][cid]['answers'] if gold else expected[cid]
            ambiguous = gold['items'][cid]['ambiguous'] if gold else []
            for criterion, value in target.items():
                total += 1
                valid = row['status'] == 'ok'
                predicted = row['answers'].get(criterion)
                hit = valid and predicted == value
                matches += hit
                if criterion not in ambiguous:
                    unambiguous_total += 1
                    unambiguous_hits += hit
                if not hit:
                    differences.append({'conversation_id': cid, 'criterion': criterion,
                                        'reference': value, 'prediction': predicted,
                                        'ambiguous': criterion in ambiguous,
                                        'reason': row['criteria'].get(criterion, {}).get('reasoning')})
        report = r['report']
        summary['runs'][name] = {'path': str(path.relative_to(ROOT)), 'model': r['meta']['config']['model'],
            'prompt_hash': r['meta']['prompt_hash'], 'checklist_hash': r['meta']['checklist_hash'],
            'reference_hash': r['meta']['reference_hash'], 'n_dialogs': len(r['conversations']),
            'n_valid': report['n_valid'], 'n_errors': report['n_errors'],
            'matches': matches, 'n_labels': total,
            'unambiguous_matches': unambiguous_hits, 'n_unambiguous': unambiguous_total,
            'mae': report['comparison']['total_mae'] if gold else None,
            'known_cost_usd': report['known_cost_usd'], 'differences': differences}
    summary['new_experiment_cost_usd'] = round(sum(v['known_cost_usd'] for k,v in summary['runs'].items() if k != 'sonnet_tuning_before'), 6)
    summary['reference_file_sha256'] = fingerprint(json.loads((ROOT / 'data/gold/dev_gold.json').read_text()))
    summary['decision'] = 'Кандидат остаётся экспериментальным. Исходная разметка и конфигурация по умолчанию не изменены. Перед продвижением нужны повторные прогоны и дополнительные семейства сценариев, особенно грубые нарушения.'
    out = ROOT / 'docs/comparisons/calibration-1-results.json'
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    for name, row in summary['runs'].items():
        print(f"{name}: {row['matches']}/{row['n_labels']}; MAE={row['mae']}; ${row['known_cost_usd']}")
    print(f"Новые прогоны: ${summary['new_experiment_cost_usd']}; {out}")


if __name__ == '__main__':
    main()
