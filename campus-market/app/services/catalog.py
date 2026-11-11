"""M5: catalog visibility and aggregations over persisted domain data."""
from __future__ import annotations

from app.core.errors import require
from app.core.filters import date_range, like_contains
from app.core.validation import integer, price, string
from app.services.auth import require_admin, require_normal


def visible_products(conn, *, keyword: str | None = None, category: str | None = None,
                     min_price: int | None = None, max_price: int | None = None,
                     availability: str | None = None, status: str | None = None,
                     limit: int = 50, offset: int = 0) -> list[dict]:
    limit = integer(limit, "limit")
    offset = integer(offset, "offset", 0)
    require(limit <= 100, "INVALID_INPUT", "limit 不能大于 100")
    where = ["p.status='ON_SALE'"]
    params: list = []
    if keyword is not None:
        keyword = like_contains(keyword, "keyword")
        where.append("(p.title LIKE ? ESCAPE '\\' OR p.description LIKE ? ESCAPE '\\')")
        params.extend([keyword, keyword])
    if category is not None:
        where.append("p.category=?")
        params.append(string(category, "category", limit=100))
    if min_price is not None:
        where.append("p.price_cents>=?")
        params.append(price(min_price))
    if max_price is not None:
        where.append("p.price_cents<=?")
        params.append(price(max_price))
    if min_price is not None and max_price is not None:
        require(min_price <= max_price, "INVALID_RANGE", "价格下限不能大于上限")
    if status is not None:
        require(status in {"ON_SALE", "AVAILABLE", "RESERVED"}, "INVALID_INPUT",
                "公开商品仅支持 ON_SALE / AVAILABLE / RESERVED 状态筛选")
        require(availability is None, "INVALID_INPUT", "status 和 availability 不能同时指定")
        availability = None if status == "ON_SALE" else status
    if availability is not None:
        require(availability in {"AVAILABLE", "RESERVED"}, "INVALID_INPUT", "无效预约筛选")
    # Derived availability, no independent product status.
    sql = """SELECT * FROM (SELECT p.*,
        EXISTS(SELECT 1 FROM reservations r
               WHERE r.product_id=p.id AND r.status IN ('PENDING','CONFIRMED')) AS booked
        FROM products p WHERE """ + " AND ".join(where) + ") WHERE 1=1"
    if availability is not None:
        sql += " AND booked=" + ("0" if availability == "AVAILABLE" else "1")
    sql += " ORDER BY published_at DESC,id DESC LIMIT ? OFFSET ?"
    return [dict(x) for x in conn.execute(sql, (*params, limit, offset))]


def seller_statistics(conn, actor: dict) -> dict:
    require_normal(actor)
    rows = conn.execute(
        "SELECT status,COUNT(*) AS n FROM products WHERE seller_id=? GROUP BY status",
        (actor["id"],),
    )
    counts = {row["status"]: row["n"] for row in rows}
    return {"total": sum(counts.values()), "by_status": counts,
            "completed_transactions": conn.execute(
                """SELECT COUNT(*) FROM reservations r JOIN products p ON p.id=r.product_id
                   WHERE p.seller_id=? AND r.status='COMPLETED'""",
                (actor["id"],),
            ).fetchone()[0]}


def platform_statistics(conn, actor: dict, start: str | None = None,
                        end: str | None = None) -> dict:
    require_admin(actor)
    start, end = date_range(start, end)
    where_pub = ["published_at IS NOT NULL"]
    where_done = ["r.status='COMPLETED'"]
    pubargs, doneargs = [], []
    if start:
        start = string(start, "start", limit=30)
        where_pub.append("substr(published_at,1,10)>=?")
        where_done.append("substr(r.completed_at,1,10)>=?")
        pubargs.append(start)
        doneargs.append(start)
    if end:
        end = string(end, "end", limit=30)
        where_pub.append("substr(published_at,1,10)<=?")
        where_done.append("substr(r.completed_at,1,10)<=?")
        pubargs.append(end)
        doneargs.append(end)
    pubsql = "SELECT substr(published_at,1,10) day,category,COUNT(*) n FROM products WHERE " + " AND ".join(where_pub) + " GROUP BY day,category ORDER BY day,category"
    donesql = """SELECT substr(r.completed_at,1,10) day,p.category,COUNT(*) n
                 FROM reservations r JOIN products p ON r.product_id=p.id WHERE """ + " AND ".join(where_done) + " GROUP BY day,p.category ORDER BY day,p.category"
    return {"published": [dict(r) for r in conn.execute(pubsql, pubargs)],
            "completed": [dict(r) for r in conn.execute(donesql, doneargs)]}




def public_facets(conn) -> dict:
    """Counts for UI-01 search controls, respecting public visibility.

    A reservation is derived from the existence of an active booking. Categories
    are not exposed until at least one product has passed review and is on sale.
    """
    rows = conn.execute("""SELECT p.category,COUNT(*) AS total,
        SUM(CASE WHEN EXISTS(
            SELECT 1 FROM reservations r WHERE r.product_id=p.id
              AND r.status IN ('PENDING','CONFIRMED')
        ) THEN 1 ELSE 0 END) AS booked,
        MIN(p.price_cents) AS min_price_cents,
        MAX(p.price_cents) AS max_price_cents
        FROM products p WHERE p.status='ON_SALE'
        GROUP BY p.category ORDER BY p.category""").fetchall()
    categories = []
    total = available = 0
    for row in rows:
        info = dict(row)
        info["available"] = info["total"] - info["booked"]
        total += info["total"]
        available += info["available"]
        categories.append(info)
    return {"total_on_sale": total, "available": available,
            "reserved": total - available, "categories": categories}


def public_product_count(conn, *, availability: str | None = None) -> int:
    """Count the public query scope without importing drafts or other statuses."""
    require(availability is None or availability in {"AVAILABLE", "RESERVED"},
            "INVALID_INPUT", "availability 筛选无效")
    predicate = """EXISTS(SELECT 1 FROM reservations r WHERE r.product_id=p.id
                   AND r.status IN ('PENDING','CONFIRMED'))"""
    sql = "SELECT COUNT(*) FROM products p WHERE p.status='ON_SALE'"
    if availability == "AVAILABLE":
        sql += " AND NOT " + predicate
    elif availability == "RESERVED":
        sql += " AND " + predicate
    return conn.execute(sql).fetchone()[0]
