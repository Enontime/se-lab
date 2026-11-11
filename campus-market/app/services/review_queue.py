"""M3 administrative review worklist, including rejected and pending products."""
from __future__ import annotations

from app.core.errors import require
from app.core.validation import detail_from_row, integer, string
from app.core.filters import like_contains
from app.db.read_models import page_bounds
from app.services.auth import require_admin


def search_pending(conn, actor: dict, *, category: str | None = None,
                   keyword: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
    """Private moderation queue; never use the public catalog for draft review."""
    require_admin(actor)
    limit, offset = page_bounds(limit, offset)
    clauses = ["p.status='PENDING_REVIEW'"]
    args = []
    if category is not None:
        clauses.append("p.category=?")
        args.append(string(category, "category", limit=100))
    if keyword is not None:
        clauses.append("p.title LIKE ? ESCAPE '\\'")
        args.append(like_contains(keyword, "keyword"))
    sql = """SELECT p.*,u.username AS seller_name,
        (SELECT COUNT(*) FROM product_reviews h WHERE h.product_id=p.id)
        AS previous_reviews FROM products p JOIN users u ON u.id=p.seller_id
        WHERE """ + " AND ".join(clauses)
    sql += " ORDER BY p.updated_at ASC,p.id ASC LIMIT ? OFFSET ?"
    return [dict(row) for row in conn.execute(sql, (*args, limit, offset))]


def review_workload(conn, actor: dict) -> dict:
    """Admin overview used to choose the next moderation view."""
    require_admin(actor)
    rows = conn.execute("""SELECT status,COUNT(*) AS n FROM products
        WHERE status IN ('PENDING_REVIEW','REJECTED','VIOLATION_DELISTED')
        GROUP BY status""")
    counts = {"PENDING_REVIEW": 0, "REJECTED": 0, "VIOLATION_DELISTED": 0}
    for row in rows:
        counts[row["status"]] = row["n"]
    counts["OPEN_VIOLATIONS"] = conn.execute("""SELECT COUNT(*)
        FROM violation_cases WHERE resolved_at IS NULL""").fetchone()[0]
    counts["RESOLVED_VIOLATIONS"] = conn.execute("""SELECT COUNT(*)
        FROM violation_cases WHERE resolved_at IS NOT NULL""").fetchone()[0]
    return counts


def admin_product_detail(conn, actor: dict, product_id: int) -> dict:
    """Admins inspect unpublished products before deciding approval or rejection.

    A public catalog lookup intentionally cannot provide draft/pending records.
    Authorization happens before the SQL query, so this method exposes no
    unpublished details to ordinary users.
    """
    require_admin(actor)
    product_id = integer(product_id, "product_id", 1)
    row = conn.execute("""SELECT p.*,u.username AS seller_name,
        EXISTS(SELECT 1 FROM reservations r WHERE r.product_id=p.id
               AND r.status IN ('PENDING','CONFIRMED')) AS booked
        FROM products p JOIN users u ON u.id=p.seller_id
        WHERE p.id=?""", (product_id,)).fetchone()
    return detail_from_row(row)


def search_reviewed(conn, actor: dict, *, decision: str,
                    limit: int = 50, offset: int = 0) -> list[dict]:
    """Most recent completed decision per product, excluding items currently pending.

    This is a moderation history view, not a public product listing.
    """
    require_admin(actor)
    require(decision in {"APPROVED", "REJECTED"}, "INVALID_DECISION", "无效审核结果")
    limit, offset = page_bounds(limit, offset)
    sql = """SELECT p.*,u.username AS seller_name,h.reviewed_at,h.reason AS review_reason
        FROM product_reviews h
        JOIN products p ON p.id=h.product_id
        JOIN users u ON u.id=p.seller_id
        WHERE h.decision=? AND p.status<>'PENDING_REVIEW'
          AND h.id=(SELECT MAX(h2.id) FROM product_reviews h2
                    WHERE h2.product_id=h.product_id)
        ORDER BY h.id DESC LIMIT ? OFFSET ?"""
    return [dict(row) for row in conn.execute(sql, (decision, limit, offset))]
