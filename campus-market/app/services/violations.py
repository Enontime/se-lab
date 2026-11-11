"""M3: violation-case lifecycle and controlled return to the review queue.

An admin's resolution closes a case, but deliberately does *not* publish a
product. The seller must resubmit; another admin must then review as usual.
"""
from __future__ import annotations

from app.core.errors import require
from app.core.validation import detail_from_row, integer, now, string
from app.services import audit
from app.services.auth import require_admin
from app.services.products import product_row


def open_case(conn, actor: dict, product_id: int, reason: str) -> dict:
    """Called only by Market's violation-delisting transaction."""
    require_admin(actor)
    product_id = integer(product_id, "product_id")
    reason = string(reason, "reason", limit=400)
    product = product_row(conn, product_id)
    require(product["status"] == "VIOLATION_DELISTED",
            "INVALID_STATUS", "只有违规下架商品可以创建违规处理记录", 409)
    require(conn.execute("""SELECT 1 FROM violation_cases WHERE product_id=?
             AND resolved_at IS NULL""", (product_id,)).fetchone() is None,
            "CASE_ALREADY_OPEN", "违规处理记录已存在", 409)
    cur = conn.execute("""INSERT INTO violation_cases
        (product_id,flagged_by,reason,created_at) VALUES (?,?,?,?)""",
        (product_id, actor["id"], reason, now()))
    return case_detail(conn, cur.lastrowid)


def case_detail(conn, case_id: int) -> dict:
    case_id = integer(case_id, "case_id")
    return detail_from_row(conn.execute("""SELECT c.*,p.status AS product_status,
        p.title AS product_title FROM violation_cases c
        JOIN products p ON p.id=c.product_id WHERE c.id=?""",
        (case_id,)).fetchone())


def current_case(conn, product_id: int) -> dict | None:
    row = conn.execute("""SELECT * FROM violation_cases
        WHERE product_id=? ORDER BY id DESC LIMIT 1""", (product_id,)).fetchone()
    return dict(row) if row else None


def is_resolved(conn, product_id: int) -> bool:
    case = current_case(conn, product_id)
    return bool(case and case["resolved_at"] is not None)


def resolve(conn, actor: dict, product_id: int, note: str) -> dict:
    """Mark the most recent open violation resolved, preserving the old reason."""
    require_admin(actor)
    product_id = integer(product_id, "product_id")
    note = string(note, "handling_note", limit=400)
    product = product_row(conn, product_id)
    require(product["status"] == "VIOLATION_DELISTED",
            "INVALID_STATUS", "当前商品不处于违规下架状态", 409)
    case = current_case(conn, product_id)
    require(case is not None and case["resolved_at"] is None,
            "INVALID_STATUS", "当前违规没有待处理记录", 409)
    conn.execute("""UPDATE violation_cases SET resolved_by=?,resolved_at=?,
        handling_note=? WHERE id=? AND resolved_at IS NULL""",
        (actor["id"], now(), note, case["id"]))
    audit.log_success(conn, actor["id"], "PRODUCT_VIOLATION_RESOLVE",
                      "Product", product_id, note)
    return {"case": case_detail(conn, case["id"]), "product": product_row(conn, product_id)}


def ready_for_resubmit(conn, product_id: int) -> None:
    require(is_resolved(conn, product_id), "INVALID_STATUS",
            "违规下架商品须先由管理员处理，才能重新提交审核", 409)


def list_cases(conn, actor: dict, *, status: str = "OPEN",
               limit: int = 50, offset: int = 0) -> list[dict]:
    """Admin worklist; admins can also inspect resolved cases for auditing."""
    from app.db.read_models import page_bounds

    require_admin(actor)
    limit, offset = page_bounds(limit, offset)
    require(status in {"OPEN", "RESOLVED", "ALL"},
            "INVALID_INPUT", "status 需为 OPEN / RESOLVED / ALL")
    where = {
        "OPEN": "WHERE c.resolved_at IS NULL",
        "RESOLVED": "WHERE c.resolved_at IS NOT NULL",
        "ALL": "",
    }[status]
    sql = """SELECT c.*,p.title AS product_title,p.status AS product_status,
        u.username AS seller_name FROM violation_cases c
        JOIN products p ON p.id=c.product_id
        JOIN users u ON u.id=p.seller_id """ + where
    sql += " ORDER BY c.id DESC LIMIT ? OFFSET ?"
    return [dict(row) for row in conn.execute(sql, (limit, offset))]


def product_cases(conn, actor: dict, product_id: int) -> list[dict]:
    """Show case history to the product's owner and admins, never the public."""
    product_id = integer(product_id, "product_id")
    p = product_row(conn, product_id)
    require(actor["account_type"] == "ADMIN" or
            (actor["account_type"] == "NORMAL" and p["seller_id"] == actor["id"]),
            "FORBIDDEN", "无权查看商品违规记录", 403)
    return [dict(row) for row in conn.execute("""SELECT c.*,
        f.username AS flagged_by_name,r.username AS resolved_by_name
        FROM violation_cases c JOIN users f ON f.id=c.flagged_by
        LEFT JOIN users r ON r.id=c.resolved_by
        WHERE c.product_id=? ORDER BY c.id DESC""", (product_id,))]
