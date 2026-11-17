"""只比對、不寫入資料庫：python -m scripts.reconcile。"""
import json

from app.pipeline import prepare_sample


def main() -> None:
    print(json.dumps(prepare_sample().report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
