"""M6: audit records committed together with business transitions."""
from __future__ import annotations

from app.core.validation import now


def log_success(conn, actor_id: int, action: str, target_type: str,
                target_id: int, reason: str | None = None) -> None:
    conn.execute(
        """INSERT INTO operation_logs
           (actor_id,action,target_type,target_id,result,reason,created_at)
           VALUES (?,?,?,?, 'SUCCESS', ?,?)""",
        (actor_id, action, target_type, target_id, reason, now()),
    )


def query_logs(conn, actor: dict, *, limit: int = 50, offset: int = 0,
               action: str | None = None, actor_id: int | None = None,
               start: str | None = None, end: str | None = None,
               target_type: str | None = None, target_id: int | None = None,
               actor_query: str | None = None) -> list[dict]:
    """M6 audit search; only administrators may access operation history."""
    from app.core.errors import require
    from app.core.validation import integer, string
    from app.core.filters import date_range
    from app.services.auth import require_admin

    require_admin(actor)
    limit = integer(limit, "limit", 1)
    offset = integer(offset, "offset", 0)
    require(limit <= 100, "INVALID_INPUT", "limit 不能大于 100")
    start, end = date_range(start, end)
    clauses, args = [], []
    if action is not None:
        clauses.append("l.action=?")
        args.append(string(action, "action", limit=100))
    if actor_query is not None:
        search = string(actor_query, "actor_query", limit=60)
        if search.isdigit():
            clauses.append("(u.username=? COLLATE NOCASE OR CAST(l.actor_id AS TEXT)=?)")
            args.extend([search, search])
        else:
            clauses.append("u.username=? COLLATE NOCASE")
            args.append(search)
    if actor_id is not None:
        clauses.append("l.actor_id=?")
        args.append(integer(actor_id, "actor_id", 1))
    if target_type is not None:
        clauses.append("l.target_type=?")
        args.append(string(target_type, "target_type", limit=100))
    if target_id is not None:
        clauses.append("l.target_id=?")
        args.append(integer(target_id, "target_id", 1))
    if start is not None:
        clauses.append("substr(l.created_at,1,10)>=?")
        args.append(_date(start, "start"))
    if end is not None:
        clauses.append("substr(l.created_at,1,10)<=?")
        args.append(_date(end, "end"))
    condition = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = ("SELECT l.*,u.username actor_name FROM operation_logs l "
           "JOIN users u ON u.id=l.actor_id" + condition +
           " ORDER BY l.id DESC LIMIT ? OFFSET ?")
    return [dict(row) for row in conn.execute(sql, (*args, limit, offset))]


def _date(value: str, field: str) -> str:
    from datetime import date
    from app.core.errors import BusinessError
    from app.core.validation import string
    value = string(value, field, limit=10)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise BusinessError("INVALID_DATE", f"{field} 必须为 YYYY-MM-DD", 400) from exc
    if parsed.isoformat() != value:
        raise BusinessError("INVALID_DATE", f"{field} 必须为 YYYY-MM-DD", 400)
    return value
