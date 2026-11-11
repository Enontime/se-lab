"""M3: audit and publish pending-review products."""
from __future__ import annotations

from app.core.errors import require
from app.core.state_rules import check_product_edge
from app.core.validation import now, string
from app.services.audit import log_success
from app.services.auth import require_admin
from app.services.products import product_row


def review(conn, actor: dict, product_id: int, decision: str, reason: str | None) -> dict:
    require_admin(actor)
    require(decision in {"APPROVED", "REJECTED"}, "INVALID_DECISION",
            "审核决定必须为 APPROVED 或 REJECTED")
    if decision == "REJECTED":
        reason = string(reason, "reason", limit=400)
    elif reason is not None:
        reason = string(reason, "reason", allow_empty=True, limit=400)
    p = product_row(conn, product_id)
    require(p["status"] == "PENDING_REVIEW", "INVALID_STATUS",
            "商品不处于待审核状态", 409)
    t = now()
    conn.execute(
        """INSERT INTO product_reviews(product_id,admin_id,decision,reason,reviewed_at)
           VALUES(?,?,?,?,?)""", (product_id, actor["id"], decision, reason, t),
    )
    target_status = "ON_SALE" if decision == "APPROVED" else "REJECTED"
    check_product_edge(p["status"], target_status)
    published_at = p["published_at"] or (t if decision == "APPROVED" else None)
    conn.execute(
        """UPDATE products SET status=?,published_at=?,version=version+1,
           updated_at=? WHERE id=?""",
        (target_status, published_at, t, product_id),
    )
    log_success(conn, actor["id"], "PRODUCT_REVIEW_" + decision,
                "Product", product_id, reason)
    return product_row(conn, product_id)
