"""Оценить готовые диалоги через Anthropic и сохранить воспроизводимый прогон."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from cqa.checklist import load_checklist
from cqa.dataset import load_records, normalize
from cqa.evaluator import EvalConfig, Evaluator
from cqa.gold import load_gold
from cqa.harness import fingerprint, save_run, summarize
from cqa.metrics import format_report


def load_examples(path, convs, gold):
    if not path.exists() or gold is None:
        return []
    ids = json.loads(path.read_text())['conversation_ids']
    return [(convs[i], gold['items'][i]) for i in ids if i in convs and i in gold['items']]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dataset', type=Path, default=ROOT / 'public/dataset.json')
    ap.add_argument('--checklist', type=Path, default=ROOT / 'config/checklist.default.json')
    ap.add_argument('--gold', type=Path, default=ROOT / 'data/gold/dev_gold.json')
    ap.add_argument('--policy', choices=['lenient', 'strict', 'any_farewell'])
    ap.add_argument('--examples', type=Path, help='Включить few-shot примеры; по умолчанию без примеров')
    ap.add_argument('--no-examples', action='store_true')
    ap.add_argument('--ids', help='ID через запятую')
    ap.add_argument('--lang')
    ap.add_argument('--channel')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--model', default='claude-opus-5-5')
    ap.add_argument('--effort', default='high', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    ap.add_argument('--votes', type=int, default=1)
    ap.add_argument('--extra-votes', type=int, default=0, help='Добор прогонов при расхождении основных (только при votes ≥ 2)')
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--max-tokens', type=int, default=16000)
    ap.add_argument('--fallback', action='store_true', help='Разрешить серверную замену модели')
    ap.add_argument('--no-fallback', action='store_true')
    ap.add_argument('--no-compare', action='store_true')
    ap.add_argument('--no-repair', action='store_true')
    ap.add_argument('--api-retries', type=int, default=2, help='Повторы SDK при транспортных ошибках; 0 отключает')
    ap.add_argument('--dry-run', action='store_true', help='Проверить входы и сохранить план без API')
    ap.add_argument('--resume', type=Path, help='Продолжить прогон, повторить ошибки')
    ap.add_argument('--compare-only', type=Path, help='Отчёт по снимкам сохранённого прогона без API')
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()
    if min(args.workers, args.votes, args.max_tokens) < 1 or (args.limit is not None and args.limit < 1):
        ap.error('workers, votes, max-tokens и limit должны быть положительными')
    if args.api_retries < 0:
        ap.error('api-retries должен быть неотрицательным')
    if args.resume and (args.out or args.dry_run):
        ap.error('--resume несовместим с --out и --dry-run')
    load_dotenv(ROOT / '.env', override=False)

    if args.compare_only:
        run = json.loads(args.compare_only.read_text())
        if run['meta'].get('dry_run'):
            ap.error('Это план dry-run, результатов API в нём нет')
        report = summarize(run['checklist'], run['reference'], run['conversations'], run['results'], run['errors'])
        save_run(args.compare_only.with_suffix('.report.json'), report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    checklist = load_checklist(args.checklist)
    if not args.no_compare and not args.gold.exists():
        ap.error('Нет файла разметки; для контрольного набора используйте --no-compare')
    gold = None if args.no_compare else load_gold(args.gold, args.policy)
    records = load_records(args.dataset)
    convs = {r['conversation_id']: normalize(r) for r in records}
    if len(convs) != len(records):
        ap.error('В наборе повторяются conversation_id')
    examples = load_examples(args.examples, convs, gold) if args.examples and not args.no_examples else []
    example_ids = {c['conversation_id'] for c, _ in examples}
    wanted = {v.strip() for v in args.ids.split(',')} if args.ids else set(convs)
    if wanted - convs.keys():
        ap.error('Неизвестные ID: ' + ', '.join(sorted(wanted - convs.keys())))
    selected = [c for k, c in convs.items() if k in wanted and k not in example_ids
                and (not args.lang or (c.get('language') or '').upper() == args.lang.upper())
                and (not args.channel or c.get('channel') == args.channel)]
    if args.limit:
        selected = selected[:args.limit]
    if not selected:
        ap.error('Фильтры не оставили диалогов')
    config = EvalConfig(model=args.model, effort=args.effort, votes=args.votes, extra_votes=args.extra_votes,
                        max_tokens=args.max_tokens, repair=not args.no_repair,
                        use_fallback=args.fallback and not args.no_fallback)
    # Подготовка промпта и схемы не требует клиента или ключа.
    evaluator = Evaluator(checklist, client=object(), config=config, examples=examples)
    identity = {
        'dataset_hash': fingerprint(records), 'checklist_hash': fingerprint(checklist),
        'api_retries': args.api_retries,
        'reference_hash': fingerprint(gold), 'config': asdict(config),
        'prompt_hash': evaluator.prompt_hash, 'selected_ids': [c['conversation_id'] for c in selected],
        'examples': sorted(example_ids), 'sdk_version': importlib.metadata.version('anthropic'),
        'code_hash': fingerprint({p.name: p.read_text() for p in sorted((ROOT / 'cqa').glob('*.py'))}),
    }
    now = datetime.now(timezone.utc).isoformat()
    run = {'meta': {**identity, 'created_at': now, 'dry_run': args.dry_run,
                    'official_ground_truth': False, 'evaluation_kind': 'dev_regression'},
           'checklist': checklist, 'reference': gold, 'conversations': selected,
           'system_prompt': evaluator.system_prompt, 'schema': evaluator.schema,
           'results': [], 'errors': []}
    out = args.resume or args.out or ROOT / 'data/runs' / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f') + ('-plan' if args.dry_run else '-eval') + '.json')
    if args.resume:
        run = json.loads(out.read_text())
        if run['meta'].get('dry_run') or any(run['meta'].get(k, 2 if k == 'api_retries' else None) != v for k, v in identity.items()):
            ap.error('Параметры или данные отличаются от сохранённого прогона; создайте новый прогон')
    elif out.exists():
        ap.error('Файл уже существует; выберите другой --out или используйте --resume')
    print(f'{len(selected)} диалогов · {config.model} · {config.votes} голос(а) · {len(examples)} примеров')
    if args.dry_run:
        save_run(out, run)
        print(f'План сохранён без вызовов API: {out}')
        return
    if not os.getenv('ANTHROPIC_API_KEY'):
        ap.error('Добавьте ANTHROPIC_API_KEY в .env проекта или окружение; ключ в чат не отправляйте')
    import anthropic
    evaluator.client = anthropic.Anthropic(timeout=120.0, max_retries=args.api_retries)
    done = {r['conversation_id'] for r in run['results']}
    pending = [c for c in selected if c['conversation_id'] not in done]
    save_run(out, run)
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(evaluator.evaluate, c): c['conversation_id'] for c in pending}
            for future in as_completed(futures):
                cid = futures[future]
                previous = [e for e in run['errors'] if e['conversation_id'] == cid]
                run['errors'] = [e for e in run['errors'] if e['conversation_id'] != cid]
                try:
                    result = future.result()
                    result['prior_errors'] = previous
                    result['calls'] = [call for e in previous for call in e.get('calls', [])] + result['calls']
                    run['results'].append(result)
                    print(f"{cid}: {result['status']} · {result['score']['total']}")
                except Exception as exc:
                    # Не сохраняем текст исключений SDK: он может содержать тело запроса.
                    run['errors'].append({'conversation_id': cid, 'error_type': type(exc.__cause__ or exc).__name__,
                                          'calls': [call for e in previous for call in e.get('calls', [])] + getattr(exc, 'calls', [])})
                    print(f'{cid}: ошибка {type(exc.__cause__ or exc).__name__}')
                run['meta']['updated_at'] = datetime.now(timezone.utc).isoformat()
                run['report'] = summarize(checklist, gold, selected, run['results'], run['errors'])
                save_run(out, run)
    finally:
        evaluator.client.close()
    report = summarize(checklist, gold, selected, run['results'], run['errors'])
    run['report'] = report
    save_run(out, run)
    save_run(out.with_suffix('.report.json'), report)
    print(f"Готово: {report['n_valid']}/{report['n_requested']} валидных · известная стоимость ${report['known_cost_usd']}\n{out}")
    if report['comparison']:
        print(format_report(report['comparison']))
    if report['n_errors'] or report['n_needs_review']:
        sys.exit(1)


if __name__ == '__main__':
    main()
