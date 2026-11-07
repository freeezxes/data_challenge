"""Скачать набор по group_id из API организаторов.

    CQA_API_URL='<адрес кнопки «Открыть GET для DEV» из гайда>' python scripts/fetch_dataset.py DEV

Адрес содержит подпись доступа — храните его в .env, не в репозитории.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cqa.dataset import fetch_dataset  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("group_id", nargs="?", default="DEV")
    ap.add_argument("--out", type=Path, help="куда сохранить (по умолчанию data/raw/<group_id>.json)")
    args = ap.parse_args()

    base = os.environ.get("CQA_API_URL")
    if not base:
        sys.exit("Задайте CQA_API_URL — адрес GET из гайда (см. .env.example)")
    payload = fetch_dataset(base, args.group_id)
    out = args.out or ROOT / "data" / "raw" / f"{args.group_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    data = payload["data"]
    print(f"{data.get('dataset_group_id')}: {data.get('total_records')} записей → {out}")


if __name__ == "__main__":
    main()
