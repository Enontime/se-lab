"""Read-only repository for user-facing product/reservation screens (U2, M2/M4).

Every query scopes private rows in SQL, rather than fetching all and filtering
in an API response. These methods never commit or change business state.
"""
from __future__ import annotations

from app.core.errors import require
from app.core.validation import detail_from_row, integer, string
from app.services.auth import require_normal

PRODUCT_STATUSES = {
    "DRAFT", "PENDING_REVIEW", "REJECTED", "ON_SALE",
    "DELISTED", "VIOLATION_DELISTED", "SOLD",
}
RESERVATION_STATUSES = {"PENDING", "CONFIRMED", "CANCELLED", "COMPLETED"}


def page_bounds(limit: int = 50, offset: int = 0) -> tuple[int, int]:
    """Bound read workload for endpoints serving paginated personal data."""
    limit = integer(limit, "limit", 1)
    offset = integer(offset, "offset", 0)
    require(limit <= 100, "INVALID_INPUT", "limit 不能超过 100")
    return limit, offset


def optional_status(value: str | None, options: set[str], field: str) -> str | None:
    if value is None:
        return None
    value = string(value, field, limit=40)
    require(value in options, "INVALID_STATUS_FILTER", f"{field} 筛选状态无效")
    return value


def owned_products(conn, actor: dict, *, status: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
    require_normal(actor)
    limit, offset = page_bounds(limit, offset)
    status = optional_status(status, PRODUCT_STATUSES, "status")
    clauses = ["p.seller_id=?"]
    params = [actor["id"]]
    if status is not None:
        clauses.append("p.status=?")
        params.append(status)
    sql = """SELECT p.*,
        EXISTS(SELECT 1 FROM reservations r WHERE r.product_id=p.id
               AND r.status IN ('PENDING','CONFIRMED')) AS booked
        FROM products p WHERE """ + " AND ".join(clauses)
    sql += " ORDER BY p.id DESC LIMIT ? OFFSET ?"
    return [dict(row) for row in conn.execute(sql, (*params, limit, offset))]


def owned_product_detail(conn, actor: dict, product_id: int) -> dict:
    """The seller may see drafts; the public detail endpoint may not."""
    require_normal(actor)
    product_id = integer(product_id, "product_id")
    row = conn.execute("""SELECT p.*,
        EXISTS(SELECT 1 FROM reservations r WHERE r.product_id=p.id
               AND r.status IN ('PENDING','CONFIRMED')) AS booked
        FROM products p WHERE p.id=? AND p.seller_id=?""",
        (product_id, actor["id"])).fetchone()
    return detail_from_row(row)


def _reservations(conn, actor: dict, *, seller_side: bool,
                  status: str | None = None, limit: int = 50,
                  offset: int = 0) -> list[dict]:
    """Keep cancellation reason and historical transactions available to both parties."""
    require_normal(actor)
    limit, offset = page_bounds(limit, offset)
    status = optional_status(status, RESERVATION_STATUSES, "status")
    where = ["p.seller_id=?" if seller_side else "r.buyer_id=?"]
    params = [actor["id"]]
    if status is not None:
        where.append("r.status=?")
        params.append(status)
    sql = """SELECT r.*,p.title AS product_title,p.price_cents AS product_price_cents,
              p.status AS product_status,p.seller_id AS seller_id,
              u.username AS counterparty_name
              FROM reservations r
              JOIN products p ON p.id=r.product_id
              JOIN users u ON u.id="""
    sql += "r.buyer_id " if seller_side else "p.seller_id "
    sql += "WHERE " + " AND ".join(where)
    sql += " ORDER BY r.id DESC LIMIT ? OFFSET ?"
    return [dict(row) for row in conn.execute(sql, (*params, limit, offset))]


def buyer_reservations(conn, actor: dict, *, status: str | None = None,
                       limit: int = 50, offset: int = 0) -> list[dict]:
    return _reservations(conn, actor, seller_side=False, status=status,
                         limit=limit, offset=offset)


def seller_reservations(conn, actor: dict, *, status: str | None = None,
                        limit: int = 50, offset: int = 0) -> list[dict]:
    return _reservations(conn, actor, seller_side=True, status=status,
                         limit=limit, offset=offset)


def reservation_detail(conn, actor: dict, reservation_id: int) -> dict:
    """A reservation is private to its buyer and the linked product's seller."""
    require_normal(actor)
    reservation_id = integer(reservation_id, "reservation_id")
    row = conn.execute("""SELECT r.*,p.title AS product_title,
        p.status AS product_status,p.price_cents AS product_price_cents,
        p.seller_id AS seller_id,u.username AS counterparty_name
        FROM reservations r JOIN products p ON p.id=r.product_id
        JOIN users u ON u.id=CASE WHEN r.buyer_id=? THEN p.seller_id
                                  ELSE r.buyer_id END
        WHERE r.id=? AND (r.buyer_id=? OR p.seller_id=?)""",
        (actor["id"], reservation_id, actor["id"], actor["id"])).fetchone()
    return detail_from_row(row)


def review_history(conn, actor: dict, product_id: int) -> list[dict]:
    """A seller can inspect feedback for their own products; admins can inspect all."""
    product_id = integer(product_id, "product_id")
    if actor["account_type"] != "ADMIN":
        require_normal(actor)
        visible = conn.execute(
            "SELECT 1 FROM products WHERE id=? AND seller_id=?",
            (product_id, actor["id"])).fetchone()
        require(visible is not None, "NOT_FOUND", "审核记录不存在或无权访问", 404)
    else:
        require(conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone(),
                "NOT_FOUND", "商品不存在", 404)
    return [dict(row) for row in conn.execute("""SELECT h.id,h.product_id,h.admin_id,
        u.username AS reviewer_name,h.decision,h.reason,h.reviewed_at
        FROM product_reviews h JOIN users u ON u.id=h.admin_id
        WHERE h.product_id=? ORDER BY h.id DESC""", (product_id,))]


def count_owned_products(conn, actor: dict) -> dict:
    """Read-only counts for the 'My products' navigation tabs."""
    require_normal(actor)
    rows = conn.execute("""SELECT status,COUNT(*) AS n
        FROM products WHERE seller_id=? GROUP BY status""", (actor["id"],))
    by_status = {key: 0 for key in sorted(PRODUCT_STATUSES)}
    for row in rows:
        by_status[row["status"]] = row["n"]
    return {"total": sum(by_status.values()), "by_status": by_status}


def count_reservations(conn, actor: dict) -> dict:
    """Count booking statuses separately in buyer and seller roles."""
    require_normal(actor)
    results = {}
    for role, condition in (
        ("as_buyer", "r.buyer_id=?"),
        ("as_seller", "p.seller_id=?"),
    ):
        rows = conn.execute("""SELECT r.status,COUNT(*) n FROM reservations r
            JOIN products p ON p.id=r.product_id
            WHERE """ + condition + " GROUP BY r.status", (actor["id"],))
        counts = {key: 0 for key in sorted(RESERVATION_STATUSES)}
        for row in rows:
            counts[row["status"]] = row["n"]
        results[role] = {"total": sum(counts.values()), "by_status": counts}
    return results
