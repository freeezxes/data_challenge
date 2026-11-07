"""Прогнать оценщик по набору и сравнить с эталоном.

    # весь DEV, 1 голос (настройка промпта)
    python scripts/run_eval.py
    # финальный прогон с голосованием
    python scripts/run_eval.py --votes 3
    # только казахские, 4 потока
    python scripts/run_eval.py --lang KZ --workers 4
    # пересчитать отчёт по готовому файлу без вызовов API
    python scripts/run_eval.py --compare-only data/runs/<файл>.json
    # контрольный набор (эталона нет — только предсказания)
    python scripts/run_eval.py --dataset data/raw/<GROUP>.json --no-compare

Каждый прогон сохраняется в data/runs/ вместе с моделью, версией промпта и стоимостью.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cqa.checklist import load_checklist  # noqa: E402
from cqa.dataset import load_records, normalize  # noqa: E402
from cqa.evaluator import EvalConfig, Evaluator  # noqa: E402
from cqa.gold import load_gold  # noqa: E402
from cqa.metrics import compare, format_report  # noqa: E402


def load_examples(path: Path, convs: dict, gold: dict | None) -> list[tuple[dict, dict]]:
    if not path.exists() or gold is None:
        return []
    ids = json.loads(path.read_text(encoding="utf-8"))["conversation_ids"]
    return [(convs[i], gold["items"][i]) for i in ids if i in convs and i in gold["items"]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, default=ROOT / "data/raw/DEV.json")
    ap.add_argument("--checklist", type=Path, default=ROOT / "config/checklist.default.json")
    ap.add_argument("--gold", type=Path, default=ROOT / "data/gold/dev_gold.json")
    ap.add_argument("--policy", default=None, help="толкование 07 в эталоне: lenient|strict|any_farewell")
    ap.add_argument("--examples", type=Path, default=ROOT / "config/examples.json")
    ap.add_argument("--no-examples", action="store_true", help="не давать примеры в промпте")
    ap.add_argument("--ids", help="через запятую")
    ap.add_argument("--lang", help="RU / KZ / EN")
    ap.add_argument("--channel", help="'agent chat' или 'voice call'")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--votes", type=int, default=1)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--no-compare", action="store_true")
    ap.add_argument("--compare-only", type=Path, help="готовый файл прогона — только отчёт")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    checklist = load_checklist(args.checklist)
    gold = None if args.no_compare or not args.gold.exists() else load_gold(args.gold, args.policy)

    if args.compare_only:
        run = json.loads(args.compare_only.read_text(encoding="utf-8"))
        preds = {r["conversation_id"]: r["answers"] for r in run["results"]}
        print(format_report(compare(checklist, gold, preds)))
        return

    if not args.dataset.exists():
        sys.exit(f"Нет файла {args.dataset}. Сначала: python scripts/fetch_dataset.py DEV")
    convs = {r["conversation_id"]: normalize(r) for r in load_records(args.dataset)}
    examples = [] if args.no_examples else load_examples(args.examples, convs, gold)
    example_ids = {c["conversation_id"] for c, _ in examples}

    selected = list(convs.values())
    if args.ids:
        wanted = set(args.ids.split(","))
        selected = [c for c in selected if c["conversation_id"] in wanted]
    if args.lang:
        selected = [c for c in selected if (c["language"] or "").upper() == args.lang.upper()]
    if args.channel:
        selected = [c for c in selected if c["channel"] == args.channel]
    selected = [c for c in selected if c["conversation_id"] not in example_ids]
    if args.limit:
        selected = selected[:args.limit]
    if not selected:
        sys.exit("Нечего оценивать: фильтры не оставили ни одного диалога")

    config = EvalConfig(model=args.model, effort=args.effort, votes=args.votes,
                        use_fallback=not args.no_fallback)
    evaluator = Evaluator(checklist, config=config, examples=examples)
    print(f"Оцениваю {len(selected)} диалогов · {config.model} · effort={config.effort} · голосов {config.votes} · "
          f"примеров в промпте {len(examples)} (исключены из замера) · prompt {evaluator.prompt_hash}")

    results, errors = [], []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(evaluator.evaluate, c): c["conversation_id"] for c in selected}
        for i, fut in enumerate(as_completed(futures), start=1):
            conv_id = futures[fut]
            try:
                res = fut.result()
                results.append(res)
                print(f"[{i}/{len(selected)}] {conv_id}: итог {res['score']['total']} · ${res['cost_usd']} · "
                      f"{res['latency_s']}s" + (f" · проблемы: {len(res['issues'])}" if res["issues"] else ""))
            except Exception as exc:  # noqa: BLE001 — фиксируем и идём дальше
                errors.append({"conversation_id": conv_id, "error": repr(exc)})
                print(f"[{i}/{len(selected)}] {conv_id}: ОШИБКА {exc!r}")

    results.sort(key=lambda r: r["conversation_id"])
    costs = [r["cost_usd"] for r in results if r["cost_usd"] is not None]
    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset), "model": config.model, "effort": config.effort, "votes": config.votes,
        "prompt_hash": evaluator.prompt_hash, "checklist_id": checklist.get("id"),
        "checklist_version": checklist.get("version"), "examples": sorted(example_ids),
        "n_results": len(results), "n_errors": len(errors),
        "cost_total_usd": round(sum(costs), 4), "cost_per_conversation_usd": round(sum(costs) / len(costs), 4) if costs else None,
        "wall_time_s": round(time.monotonic() - started, 1),
    }
    out = args.out or ROOT / "data/runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{config.model}-v{config.votes}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "results": results, "errors": errors}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\nСохранено: {out}\nСтоимость: ${meta['cost_total_usd']} всего, ${meta['cost_per_conversation_usd']} за диалог")

    if gold:
        preds = {r["conversation_id"]: r["answers"] for r in results}
        report = compare(checklist, gold, preds)
        print("\n" + format_report(report))
        out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
