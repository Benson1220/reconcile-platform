"""匯出可分享的原始碼作品包；只包含明確允許的專案路徑。"""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist/reconcile-platform-portfolio.zip"
FILES = ["README.md", "pyproject.toml", "requirements-lock.txt", ".gitignore", ".env.example"]
DIRECTORIES = ["app", "sql", "scripts", "tests", "docs", "data/sample"]
SKIP = {"__pycache__", ".venv", ".local", ".git", ".pytest_cache"}


def export() -> tuple[Path, int]:
    paths = [ROOT / name for name in FILES]
    for name in DIRECTORIES:
        paths.extend(path for path in (ROOT / name).rglob("*") if path.is_file())
    selected = sorted({path for path in paths if path.is_file() and not path.is_symlink()
                       and not any(part in SKIP for part in path.relative_to(ROOT).parts)
                       and (not path.name.startswith(".env") or path.name == ".env.example")
                       and path.suffix not in {".pyc", ".pyo"}
                       and path.name != ".DS_Store"})
    OUTPUT.parent.mkdir(exist_ok=True)
    with ZipFile(OUTPUT, "w", compression=ZIP_DEFLATED) as archive:
        for path in selected:
            archive.write(path, Path("reconcile-platform") / path.relative_to(ROOT))
    return OUTPUT, len(selected)


if __name__ == "__main__":
    output, count = export()
    print(f"OK 已匯出 {count} 個檔案：{output}")
    print("只包含原始碼、合成範例與文件；不包含 .env、資料庫目錄或虛擬環境。")
