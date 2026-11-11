"""Input validation and deterministic canonicalization."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from app.core.errors import BusinessError, require

USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def string(value: object, field: str, *, allow_empty: bool = False, limit: int = 1000) -> str:
    require(isinstance(value, str), "INVALID_INPUT", f"{field} 必须是字符串")
    result = value.strip()
    require(len(result) <= limit, "INVALID_INPUT", f"{field} 长度不能超过 {limit}")
    require(allow_empty or bool(result), "INVALID_INPUT", f"{field} 不能为空")
    return result


def integer(value: object, field: str, minimum: int = 1) -> int:
    require(type(value) is int and value >= minimum,
            "INVALID_INPUT", f"{field} 必须是大于等于 {minimum} 的整数")
    return value


def price(value: object) -> int:
    cents = integer(value, "price_cents", 0)
    # The schema stores SQLite signed 64-bit integers. Reject overflow as a
    # validation error instead of letting SQLite raise a server exception.
    require(cents <= 2**63 - 1, "INVALID_INPUT", "价格超出数据库支持范围")
    return cents


def normalized_username(username: str) -> str:
    require(bool(USERNAME.fullmatch(username)), "INVALID_USERNAME",
            "用户名仅支持 3-32 位英文、数字、下划线、点或连字符")
    return username.casefold()


def fingerprint(product_id: int, version: int, price_cents: int) -> str:
    canonical = json.dumps([product_id, version, price_cents], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def detail_from_row(row) -> dict:
    if row is None:
        raise BusinessError("NOT_FOUND", "记录不存在", 404)
    return dict(row)
