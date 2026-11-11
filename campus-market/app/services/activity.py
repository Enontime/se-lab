"""M6: product-specific audit timeline and administrative activity summaries.

These are read-only management screens. Business actions still write logs inside
Market's original transactions; no independent audit write transaction exists.
"""
from __future__ import annotations

from app.core.filters import date_range
from app.core.validation import integer
from app.db.read_models import page_bounds
from app.services.auth import require_admin
from app.services.products import product_row


def product_timeline(conn, actor: dict, product_id: int, *,
                     limit: int = 50, offset: int = 0) -> list[dict]:
    """Review all product-related and linked reservation operations together."""
    require_admin(actor)
    product_id = integer(product_id, "product_id")
    product_row(conn, product_id)
    limit, offset = page_bounds(limit, offset)
    return [dict(row) for row in conn.execute("""SELECT l.id,l.actor_id,
        u.username AS actor_name,l.action,l.target_type,l.target_id,
        l.result,l.reason,l.created_at FROM operation_logs l
        JOIN users u ON u.id=l.actor_id
        WHERE (l.target_type='Product' AND l.target_id=?)
           OR (l.target_type='Reservation' AND EXISTS (
               SELECT 1 FROM reservations r
               WHERE r.id=l.target_id AND r.product_id=?))
        ORDER BY l.id DESC LIMIT ? OFFSET ?""",
        (product_id, product_id, limit, offset))]


def activity_summary(conn, actor: dict, *, start: str | None = None,
                     end: str | None = None, limit: int = 20) -> dict:
    """Counts by action and day, for admin UI-11 without transferring all logs."""
    require_admin(actor)
    start, end = date_range(start, end)
    limit, _ = page_bounds(limit, 0)
    where = []
    params = []
    if start is not None:
        where.append("substr(created_at,1,10)>=?")
        params.append(start)
    if end is not None:
        where.append("substr(created_at,1,10)<=?")
        params.append(end)
    condition = " WHERE " + " AND ".join(where) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM operation_logs" + condition,
                         params).fetchone()[0]
    action_sql = """SELECT action,COUNT(*) AS n FROM operation_logs """ + condition
    action_sql += " GROUP BY action ORDER BY n DESC,action ASC LIMIT ?"
    daily_sql = """SELECT substr(created_at,1,10) AS day,COUNT(*) AS n
       FROM operation_logs """ + condition
    daily_sql += " GROUP BY day ORDER BY day DESC LIMIT ?"
    return {
        "total": total,
        "by_action": [dict(row) for row in conn.execute(action_sql, (*params, limit))],
        "by_day": [dict(row) for row in conn.execute(daily_sql, (*params, limit))],
    }


def user_activity(conn, actor: dict, user_id: int, *,
                  limit: int = 50, offset: int = 0) -> dict:
    """Audit who changed data: only admins may query another account."""
    require_admin(actor)
    user_id = integer(user_id, "user_id")
    limit, offset = page_bounds(limit, offset)
    user = conn.execute("SELECT id,username,account_type FROM users WHERE id=?",
                        (user_id,)).fetchone()
    from app.core.errors import require
    require(user is not None, "NOT_FOUND", "用户不存在", 404)
    events = [dict(row) for row in conn.execute("""SELECT * FROM operation_logs
        WHERE actor_id=? ORDER BY id DESC LIMIT ? OFFSET ?""",
        (user_id, limit, offset))]
    return {"user": dict(user), "events": events}
