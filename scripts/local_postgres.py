"""管理專案專用 PostgreSQL，不建立開機啟動服務。"""
import argparse
import json
import secrets
import shutil
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local"
DATA = STATE / "postgres"
SOCKET = STATE / "socket"
PORT = 55432


def binary(name: str) -> str:
    for directory in [Path("/opt/homebrew/opt/postgresql@17/bin"), Path("/usr/local/opt/postgresql@17/bin")]:
        if (directory / name).exists():
            return str(directory / name)
    found = shutil.which(name)
    if found:
        return found
    raise ValueError("找不到 PostgreSQL 17，請先安裝 brew install postgresql@17")


def is_running() -> bool:
    return DATA.exists() and subprocess.run(
        [binary("pg_ctl"), "-D", str(DATA), "status"], capture_output=True,
    ).returncode == 0


def start() -> None:
    if not (DATA / "PG_VERSION").exists():
        raise ValueError("尚未初始化，請先執行 python -m scripts.local_postgres setup")
    if not is_running():
        subprocess.run([binary("pg_ctl"), "-D", str(DATA), "-l", str(STATE / "postgres.log"), "-w", "start"], check=True)
    print(f"OK PostgreSQL 已啟動，僅監聽 127.0.0.1:{PORT}")


def private_write(path: Path, content: str) -> None:
    # 先設定權限再寫入任何密碼。
    with path.open("x", encoding="utf-8") as file:
        path.chmod(0o600)
        file.write(content)


def setup() -> None:
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    SOCKET.mkdir(mode=0o700, exist_ok=True)
    credentials = STATE / "credentials.json"
    if not credentials.exists():
        if (ROOT / ".env").exists() or DATA.exists():
            raise ValueError("已有 .env 或資料庫目錄；為避免覆寫，請先確認既有設定")
        private_write(credentials, json.dumps({"admin_password": secrets.token_hex(24), "app_password": secrets.token_hex(24)}))
    values = json.loads(credentials.read_text())
    if not (DATA / "PG_VERSION").exists():
        password_file = STATE / "init-password"
        private_write(password_file, values["admin_password"])
        try:
            subprocess.run([
                binary("initdb"), "-D", str(DATA), "-U", "reconcile_admin",
                "--encoding=UTF8", "--locale=C", "--auth=scram-sha-256",
                f"--pwfile={password_file}",
            ], check=True)
        finally:
            password_file.unlink(missing_ok=True)
        with (DATA / "postgresql.conf").open("a") as file:
            file.write(f"\nlisten_addresses = '127.0.0.1'\nport = {PORT}\n"
                       f"unix_socket_directories = '{SOCKET}'\n"
                       "unix_socket_permissions = 0700\nshared_buffers = '32MB'\nmax_connections = 20\n")
    start()
    with psycopg.connect(host="127.0.0.1", port=PORT, user="reconcile_admin",
                         password=values["admin_password"], dbname="postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='reconcile_app'").fetchone():
            conn.execute(sql.SQL("CREATE ROLE reconcile_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Literal(values["app_password"])))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='reconcile'").fetchone():
            conn.execute("CREATE DATABASE reconcile OWNER reconcile_app")
    env = ROOT / ".env"
    if not env.exists():
        private_write(env, f"DATABASE_URL=postgresql://reconcile_app:{values['app_password']}@127.0.0.1:{PORT}/reconcile\n")
    print("OK reconcile 資料庫與一般應用程式帳號就緒；連線設定保存在 .env（不顯示密碼）")


def main() -> None:
    parser = argparse.ArgumentParser(description="管理本專案的 PostgreSQL")
    parser.add_argument("action", choices=["setup", "start", "stop", "status"])
    action = parser.parse_args().action
    if action == "setup":
        setup()
    elif action == "start":
        start()
    elif action == "stop":
        if is_running():
            subprocess.run([binary("pg_ctl"), "-D", str(DATA), "-m", "fast", "-w", "stop"], check=True)
        print("OK PostgreSQL 已停止；資料保留")
    else:
        print("PostgreSQL：執行中" if is_running() else "PostgreSQL：已停止或尚未初始化")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, psycopg.Error) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
