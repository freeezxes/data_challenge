"""Сводка по эталонной разметке: распределение ответов, итоговые баллы, спорные места.

    python scripts/gold_report.py [--policy lenient|strict|any_farewell] [--dataset data/raw/DEV.json]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cqa.checklist import load_checklist  # noqa: E402
from cqa.dataset import load_records  # noqa: E402
from cqa.gold import load_gold  # noqa: E402
from cqa.scoring import score  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, default=ROOT / "data/gold/dev_gold.json")
    ap.add_argument("--checklist", type=Path, default=ROOT / "config/checklist.default.json")
    ap.add_argument("--policy", default=None, help="толкование критерия 07")
    ap.add_argument("--dataset", type=Path, default=ROOT / "data/raw/DEV.json",
                    help="если файл есть — проверить, что ID совпадают с набором")
    args = ap.parse_args()

    checklist = load_checklist(args.checklist)
    gold = load_gold(args.gold, args.policy)
    items = gold["items"]
    print(f"Эталон: {len(items)} диалогов, политика 07: {gold['policy']}\n")

    for c in checklist["criteria"]:
        dist = Counter(str(it["answers"][c["id"]]) for it in items.values())
        amb = sum(c["id"] in it["ambiguous"] for it in items.values())
        print(f"{c['id']} {c['title']:<45} {dict(sorted(dist.items()))}  спорных: {amb}")

    print("\nИтоговые баллы:")
    for conv_id, it in items.items():
        s = score(checklist, it["answers"])
        flag = " ⚠ грубая ошибка" if s["critical"] else ""
        amb = f"  спорно: {','.join(it['ambiguous'])}" if it["ambiguous"] else ""
        print(f"  {conv_id}: {s['total']}{flag}{amb}")

    if args.dataset.exists():
        ids = {r["conversation_id"] for r in load_records(args.dataset)}
        missing, extra = ids - set(items), set(items) - ids
        print(f"\nСверка с набором: нет в эталоне {sorted(missing) or '—'}; лишние {sorted(extra) or '—'}")


if __name__ == "__main__":
    main()
