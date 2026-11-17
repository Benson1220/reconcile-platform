"""python -m scripts.db init | import-sample | show"""
import argparse
import json

import psycopg

from app.database import initialize_database, read_summary, save_snapshot
from app.pipeline import prepare_sample


def main() -> None:
    parser = argparse.ArgumentParser(description="資料庫建表、匯入與查詢")
    parser.add_argument("action", choices=["init", "import-sample", "show"])
    action = parser.parse_args().action
    if action == "init":
        versions = initialize_database()
        print(f"OK 資料庫 schema 已就緒（migration {', '.join(map(str, versions))}）")
    elif action == "import-sample":
        print(json.dumps(save_snapshot(prepare_sample()), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(read_summary(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, psycopg.Error) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
