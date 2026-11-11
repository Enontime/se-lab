"""Shared safe parsing for search terms, time ranges and pagination.

The database query builders use bound parameters; the only dynamic SQL pieces
are fixed internally authored clauses, never field names from user input.
"""
from __future__ import annotations

from datetime import date

from app.core.errors import BusinessError, require
from app.core.validation import integer, string


def iso_day(value: str, field: str) -> str:
    value = string(value, field, limit=10)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise BusinessError("INVALID_DATE", f"{field} 必须为 YYYY-MM-DD", 400) from exc
    require(parsed.isoformat() == value, "INVALID_DATE", f"{field} 必须为 YYYY-MM-DD")
    return value


def date_range(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    """Canonicalize each boundary before comparing, including invalid leap days."""
    start = iso_day(start, "start") if start is not None else None
    end = iso_day(end, "end") if end is not None else None
    require(start is None or end is None or start <= end,
            "INVALID_RANGE", "日期起点不能晚于终点")
    return start, end


def like_contains(value: str, field: str, *, limit: int = 100) -> str:
    """Literal LIKE term: escape %, _ and backslash, then add substring wildcards."""
    value = string(value, field, allow_empty=True, limit=limit)
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def pagination(limit: int = 50, offset: int = 0, *, cap: int = 100) -> tuple[int, int]:
    limit = integer(limit, "limit", 1)
    offset = integer(offset, "offset", 0)
    require(limit <= cap, "INVALID_INPUT", f"limit 不能大于 {cap}")
    return limit, offset


def integer_query(value: str | None, field: str,
                  default: int | None = None, minimum: int = 0) -> int | None:
    """Do not accept decimal, scientific notation, blanks or booleans from query."""
    if value is None:
        return default
    require(value.strip() == value and value and
            (value.isdecimal() or (value.startswith("-") and value[1:].isdecimal())),
            "INVALID_QUERY", f"{field} 必须是整数")
    return integer(int(value), field, minimum)


def guard_fields(data: dict, *, permitted: set[str], required: set[str] | None = None) -> dict:
    """Reject unknown JSON keys before a command could silently ignore them."""
    require(isinstance(data, dict), "INVALID_JSON", "请求体必须是 JSON 对象")
    unknown = set(data).difference(permitted)
    require(not unknown, "UNKNOWN_FIELD", "请求体含不支持的字段: " + ", ".join(sorted(unknown)))
    missing = (required or set()).difference(data)
    require(not missing, "MISSING_FIELD", "请求体缺少字段: " + ", ".join(sorted(missing)))
    return data
