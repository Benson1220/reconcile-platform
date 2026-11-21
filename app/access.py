"""Account authentication for the local workbench."""
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

import psycopg

ROLES = ("viewer", "investigator", "supervisor")
SESSION_COOKIE = "reconcile_session"
SESSION_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class Actor:
    username: str
    role: str


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = secrets.token_bytes(16) if salt is None else salt
    key = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{key.hex()}"


def create_user(dsn: str, username: str, role: str, password: str) -> None:
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(
            "INSERT INTO app_users (username, role, password_hash) VALUES (%s,%s,%s)",
            (username, role, password_hash(password)),
        )


def authenticate(dsn: str, username: str, password: str) -> Actor | None:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", username) or len(password.encode()) > 256:
        return None
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT role, password_hash FROM app_users WHERE username=%s", (username,),
        ).fetchone()
    # Run the same password calculation for unknown usernames.
    expected = row[1] if row else "0" * 32 + ":" + "0" * 128
    calculated = password_hash(password, bytes.fromhex(expected.split(":", 1)[0]))
    if hmac.compare_digest(expected, calculated) and row:
        return Actor(username, row[0])
    return None


def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(dsn: str, username: str, previous_token: str = "") -> str:
    token = secrets.token_urlsafe(32)
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(
            "DELETE FROM login_sessions WHERE expires_at <= now() OR token_hash=%s",
            (session_digest(previous_token),),
        )
        conn.execute(
            "INSERT INTO login_sessions VALUES (%s,%s,now() + %s * interval '1 second')",
            (session_digest(token), username, SESSION_SECONDS),
        )
    return token


def session_actor(dsn: str, token: str) -> Actor | None:
    if not token or len(token) != 43:
        return None
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        row = conn.execute(
            """SELECT u.username, u.role FROM login_sessions s
               JOIN app_users u ON u.username=s.username
               WHERE s.token_hash=%s AND s.expires_at > now()""",
            (session_digest(token),),
        ).fetchone()
    return Actor(*row) if row else None


def end_session(dsn: str, token: str) -> None:
    if token:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute("DELETE FROM login_sessions WHERE token_hash=%s", (session_digest(token),))
