"""在自己的終端機安全輸入 API 金鑰，不透過聊天或網頁表單。"""
from getpass import getpass
from pathlib import Path

from dotenv import set_key

from app.ai_summary import DEFAULT_MODEL
from app.database import ROOT


def write_settings(path: Path, key: str) -> None:
    key = key.strip()
    if not key or any(char.isspace() for char in key):
        raise ValueError("金鑰不可空白或包含空白字元")
    if not key.startswith("sk-"):
        raise ValueError("請貼上 Create new secret key 產生的完整 OpenAI API 金鑰（通常以 sk- 開頭），不是金鑰名稱或專案 ID；本機設定尚未修改")
    if not path.exists():
        path.touch(mode=0o600)
    path.chmod(0o600)
    set_key(str(path), "OPENAI_API_KEY", key)
    set_key(str(path), "OPENAI_MODEL", DEFAULT_MODEL)
    path.chmod(0o600)


if __name__ == "__main__":
    try:
        write_settings(ROOT / ".env", getpass("請輸入 OpenAI API 金鑰（輸入時不會顯示）："))
        print("OK 金鑰已存入本機 .env，既有資料庫設定已保留。請重新啟動後端。")
    except (ValueError, OSError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
