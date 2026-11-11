"""M2: product state transitions and ownership rules."""
from __future__ import annotations

from app.core.errors import BusinessError, require
from app.core.state_rules import check_product_edge
from app.core.validation import detail_from_row, now, price, string
from app.services.audit import log_success
from app.services.auth import require_normal

EDITABLE = {"title", "category", "price_cents", "item_condition", "description", "trade_location"}
REQUIRED = ("title", "category", "price_cents", "item_condition", "description", "trade_location")


def product_row(conn, product_id: int) -> dict:
    row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    return detail_from_row(row)


def own_product(conn, product_id: int, actor: dict) -> dict:
    require_normal(actor)
    p = product_row(conn, product_id)
    require(p["seller_id"] == actor["id"], "FORBIDDEN", "不能修改其他人的商品", 403)
    return p


def active_booking(conn, product_id: int) -> bool:
    return conn.execute("""SELECT EXISTS(
        SELECT 1 FROM reservations WHERE product_id=? AND status IN ('PENDING','CONFIRMED')
    )""", (product_id,)).fetchone()[0] == 1


def normalize_changes(data: dict) -> dict:
    require(isinstance(data, dict), "INVALID_INPUT", "商品信息必须为对象")
    require(bool(data) and set(data).issubset(EDITABLE),
            "INVALID_INPUT", "商品字段为空或包含未知字段")
    return {k: price(v) if k == "price_cents" else string(v, k, limit=1000)
            for k, v in data.items()}


def create_draft(conn, actor: dict, fields: dict) -> dict:
    require_normal(actor)
    data = normalize_changes(fields)
    timestamp = now()
    cur = conn.execute(
        """INSERT INTO products (seller_id,title,category,price_cents,item_condition,
               description,trade_location,status,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,'DRAFT',?,?)""",
        (actor["id"], *(data.get(k) for k in EDITABLE_ORDER), timestamp, timestamp),
    )
    log_success(conn, actor["id"], "PRODUCT_DRAFT_CREATE", "Product", cur.lastrowid)
    return product_row(conn, cur.lastrowid)


EDITABLE_ORDER = ("title", "category", "price_cents", "item_condition", "description", "trade_location")


def edit_draft(conn, actor: dict, product_id: int, changes: dict) -> dict:
    p = own_product(conn, product_id, actor)
    require(p["status"] in {"DRAFT", "REJECTED"},
            "INVALID_STATUS", "只有草稿或审核驳回的商品可直接编辑", 409)
    data = normalize_changes(changes)
    assignments = ", ".join(f"{name}=?" for name in data)
    conn.execute(f"UPDATE products SET {assignments},updated_at=?,version=version+1 WHERE id=?",
                 (*data.values(), now(), product_id))
    log_success(conn, actor["id"], "PRODUCT_DRAFT_EDIT", "Product", product_id)
    return product_row(conn, product_id)


def submit_review(conn, actor: dict, product_id: int) -> dict:
    p = own_product(conn, product_id, actor)
    require(p["status"] in {"DRAFT", "REJECTED", "DELISTED", "VIOLATION_DELISTED"},
            "INVALID_STATUS", "此商品不能直接提交审核", 409)
    if p["status"] == "VIOLATION_DELISTED":
        # U4: administrator resolves the incident first; seller then requests
        # a normal review, with no automatic publication or approval.
        from app.services.violations import ready_for_resubmit
        ready_for_resubmit(conn, product_id)
    for field in REQUIRED:
        require(p[field] is not None and (field == "price_cents" or bool(p[field])),
                "INCOMPLETE_PRODUCT", f"提交审核前必须填写 {field}")
    check_product_edge(p["status"], "PENDING_REVIEW")
    conn.execute("""UPDATE products SET status='PENDING_REVIEW', version=version+1,
                    updated_at=? WHERE id=?""", (now(), product_id))
    log_success(conn, actor["id"], "PRODUCT_SUBMIT", "Product", product_id)
    return product_row(conn, product_id)


def edit_on_sale_core(conn, actor: dict, product_id: int, changes: dict) -> dict:
    """REQ-005 V1.2: edit any of six fields, unpublish into DRAFT.

    Submission and approval are separate actions. The coordinator opens a
    BEGIN IMMEDIATE transaction, so the booking check and state change are
    atomic with respect to competing reservations.
    """
    p = own_product(conn, product_id, actor)
    require(p["status"] == "ON_SALE", "INVALID_STATUS", "商品当前未在售", 409)
    data = normalize_changes(changes)
    # Do not delist a product just because an unchanged form was submitted.
    changed = {field: value for field, value in data.items() if p[field] != value}
    require(changed, "NO_CHANGES", "商品信息没有变化，无需重新审核", 400)
    require(not active_booking(conn, product_id), "HAS_BOOKING",
            "有有效预约，不能修改商品信息", 409)
    check_product_edge(p["status"], "DRAFT")
    assignments = ", ".join(f"{name}=?" for name in changed)
    conn.execute(
        f"UPDATE products SET {assignments},status='DRAFT',"
        "version=version+1,updated_at=? WHERE id=?",
        (*changed.values(), now(), product_id),
    )
    log_success(conn, actor["id"], "PRODUCT_EDIT_TO_DRAFT", "Product", product_id)
    return product_row(conn, product_id)


def apply_delist(conn, product_id: int, *, violation: bool = False,
                 reason: str | None = None) -> dict:
    """M2-only state mutation. Authorization and booking cleanup belong to coordinator."""
    p = product_row(conn, product_id)
    require(p["status"] == "ON_SALE", "INVALID_STATUS", "仅在售商品可下架", 409)
    new_status = "VIOLATION_DELISTED" if violation else "DELISTED"
    check_product_edge(p["status"], new_status)
    conn.execute(
        """UPDATE products SET status=?,version=version+1,updated_at=?,violation_reason=?
           WHERE id=?""", (new_status, now(), reason if violation else None, product_id),
    )
    return product_row(conn, product_id)


def mark_sold(conn, product_id: int) -> dict:
    """M2-only terminal transition; caller verifies reservation and transaction."""
    p = product_row(conn, product_id)
    require(p["status"] == "ON_SALE", "INVALID_STATUS", "只有在售商品可以成交", 409)
    check_product_edge(p["status"], "SOLD")
    conn.execute("""UPDATE products SET status='SOLD',version=version+1,updated_at=?
                    WHERE id=?""", (now(), product_id))
    return product_row(conn, product_id)
