"""Загрузить в базу продукта результаты прогона из scripts/run_eval.py.

    python -m app.import_run data/runs/opus-v9-dev.json [--dataset DEV]

Оценки помечаются источником «прогон CLI», чек-лист берётся из снимка прогона.
Повторный импорт того же файла ничего не дублирует.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .server import ROOT, App


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run", type=Path)
    ap.add_argument("--dataset", default="DEV")
    args = ap.parse_args()
    run = json.loads(args.run.read_text(encoding="utf-8"))
    app = App(os.environ.get("CQA_DB", ROOT / "data/cqa.db"))
    key = app.store.import_checklist(run["checklist"], "снимок из прогона CLI")
    source = f"прогон CLI {args.run.name}"
    known = {r["conversation_id"] for r in app.store.q("select conversation_id from evaluations where source=?", (source,))}
    n = 0
    for r in run["results"]:
        if r["conversation_id"] in known:
            continue
        r = {k: v for k, v in r.items() if k != "calls"}
        app.store.add_evaluation(args.dataset, r["conversation_id"], key, r["status"], r, source=source)
        n += 1
    print(f"Импортировано {n} оценок, чек-лист {key}")
    app.pool.shutdown(wait=False)


if __name__ == "__main__":
    main()
