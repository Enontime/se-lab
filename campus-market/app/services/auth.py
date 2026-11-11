"""M1: account creation, role checks, opaque API bearer sessions."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from app.core.errors import BusinessError, require
from app.core.validation import now, normalized_username, string
from app.services.audit import log_success

HASH_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    require(isinstance(password, str) and 8 <= len(password) <= 128,
            "INVALID_PASSWORD", "密码长度需要为 8～128 字符")
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ITERATIONS)
    return f"pbkdf2_sha256${HASH_ITERATIONS}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not isinstance(password, str):
        return False
    try:
        method, rounds, salt_hex, key_hex = stored.split("$")
        if method != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                        bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(candidate, bytes.fromhex(key_hex))
    except (ValueError, TypeError):
        return False


def create_user(conn, username: str, password: str, *, admin: bool = False) -> dict:
    username = string(username, "username", limit=32)
    key = normalized_username(username)
    encoded = hash_password(password)
    try:
        cur = conn.execute(
            """INSERT INTO users(username,username_key,password_hash,account_type,created_at)
               VALUES (?,?,?,?,?)""",
            (username, key, encoded, "ADMIN" if admin else "NORMAL", now()),
        )
    except sqlite3.IntegrityError as exc:
        raise BusinessError("USERNAME_EXISTS", "用户名已存在", 409) from exc
    return {"id": cur.lastrowid, "username": username,
            "account_type": "ADMIN" if admin else "NORMAL"}


def get_actor(conn, user_id: int) -> dict:
    row = conn.execute("SELECT id,username,account_type FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise BusinessError("UNAUTHORIZED", "请先登录", 401)
    return dict(row)


def require_normal(actor: dict) -> None:
    require(actor["account_type"] == "NORMAL", "FORBIDDEN", "普通用户专属操作", 403)


def require_admin(actor: dict) -> None:
    require(actor["account_type"] == "ADMIN", "FORBIDDEN", "管理员专属操作", 403)


def issue_token(conn, username: str, password: str) -> tuple[str, dict]:
    username = string(username, "username", limit=32)
    key = normalized_username(username)
    row = conn.execute("SELECT * FROM users WHERE username_key=?", (key,)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        raise BusinessError("BAD_CREDENTIALS", "账号或密码错误", 401)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    conn.execute(
        "INSERT INTO auth_sessions(user_id,token_hash,created_at,expires_at) VALUES(?,?,?,?)",
        (row["id"], token_hash, now(), expires.isoformat(timespec="seconds")),
    )
    return token, {"id": row["id"], "username": row["username"],
                   "account_type": row["account_type"]}


def resolve_token(conn, raw_token: str) -> dict:
    if not raw_token:
        raise BusinessError("UNAUTHORIZED", "需要 Bearer Token", 401)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    row = conn.execute(
        """SELECT u.id,u.username,u.account_type FROM auth_sessions s
           JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.expires_at > ?""",
        (token_hash, now()),
    ).fetchone()
    if row is None:
        raise BusinessError("UNAUTHORIZED", "登录已失效", 401)
    return dict(row)


def revoke_token(conn, raw_token: str) -> None:
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
