"""Create a workbench account without putting its password in shell history."""
import argparse
import getpass
import re

import psycopg

from app.access import ROLES, create_user
from app.database import database_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("role", choices=ROLES)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", args.username):
        parser.error("帳號須以小寫英文字母開頭，共 2～32 字元，可包含數字、底線或連字號。")
    password = getpass.getpass("密碼（輸入時不顯示）：")
    if len(password) < 12 or len(password.encode("utf-8")) > 256:
        parser.error("密碼至少須有 12 個字元，且不得超過 256 位元組。")
    if password != getpass.getpass("再輸入一次密碼："):
        parser.error("兩次輸入的密碼不同。")
    try:
        create_user(database_url(), args.username, args.role, password)
    except psycopg.errors.UniqueViolation:
        parser.error("帳號已存在。")
    print(f"已建立帳號 {args.username}（{args.role}）")


if __name__ == "__main__":
    main()
