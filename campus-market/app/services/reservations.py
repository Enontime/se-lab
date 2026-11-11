"""M4: reservation lifecycle with transaction-level product coordination."""
from __future__ import annotations

from app.core.errors import BusinessError, require
from app.core.state_rules import check_reservation_edge
from app.core.validation import detail_from_row, fingerprint, integer, now, string
from app.services.audit import log_success
from app.services.auth import require_normal
from app.services.products import active_booking, product_row


def booking_row(conn, reservation_id: int) -> dict:
    return detail_from_row(conn.execute(
        "SELECT * FROM reservations WHERE id=?", (reservation_id,)).fetchone())


def _idempotent(conn, actor_id: int, request_id: str, expected_fp: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM reservations WHERE buyer_id=? AND request_id=?",
        (actor_id, request_id),
    ).fetchone()
    if row is None:
        return None
    require(row["request_fingerprint"] == expected_fp,
            "REQUEST_REUSED", "同一请求 ID 被用于不同预约参数", 409)
    return dict(row)


def create(conn, actor: dict, product_id: int, request_id: str,
           expected_version: int, expected_price_cents: int) -> dict:
    """Caller begins IMMEDIATE write transaction before any state-dependent read."""
    require_normal(actor)
    product_id = integer(product_id, "product_id")
    expected_version = integer(expected_version, "expected_version")
    expected_price_cents = integer(expected_price_cents, "expected_price_cents", 0)
    request_id = string(request_id, "request_id", limit=128)
    fp = fingerprint(product_id, expected_version, expected_price_cents)
    found = _idempotent(conn, actor["id"], request_id, fp)
    if found is not None:
        return {"reservation": found, "idempotent_replay": True}
    p = product_row(conn, product_id)
    require(p["seller_id"] != actor["id"], "OWN_PRODUCT", "不能预约自己的商品", 403)
    require(p["status"] == "ON_SALE", "NOT_ON_SALE", "商品当前不在售", 409)
    require(p["version"] == expected_version and p["price_cents"] == expected_price_cents,
            "STALE_PRODUCT", "商品信息已变化，请刷新后重新预约", 409)
    require(not active_booking(conn, product_id), "PRODUCT_RESERVED", "商品已被预约", 409)
    t = now()
    # A partial UNIQUE index protects against duplicate active bookings even
    # if a future caller is not following the application-level pre-check.
    cur = conn.execute(
        """INSERT INTO reservations
            (product_id,buyer_id,request_id,request_fingerprint,
             expected_version,expected_price_cents,status,created_at)
            VALUES(?,?,?,?,?,?,'PENDING',?)""",
        (product_id, actor["id"], request_id, fp,
         expected_version, expected_price_cents, t),
    )
    log_success(conn, actor["id"], "RESERVATION_CREATE", "Reservation", cur.lastrowid)
    return {"reservation": booking_row(conn, cur.lastrowid), "idempotent_replay": False}


def _seller_booking(conn, actor: dict, reservation_id: int) -> tuple[dict, dict]:
    require_normal(actor)
    booking = booking_row(conn, reservation_id)
    product = product_row(conn, booking["product_id"])
    require(product["seller_id"] == actor["id"], "FORBIDDEN",
            "只能处理自己商品的预约", 403)
    return booking, product


def confirm(conn, actor: dict, reservation_id: int) -> dict:
    b, p = _seller_booking(conn, actor, reservation_id)
    require(b["status"] == "PENDING" and p["status"] == "ON_SALE",
            "INVALID_STATUS", "只有在售商品的待确认预约可以确认", 409)
    check_reservation_edge(b["status"], "CONFIRMED")
    conn.execute("""UPDATE reservations SET status='CONFIRMED',confirmed_at=?
                    WHERE id=?""", (now(), reservation_id))
    log_success(conn, actor["id"], "RESERVATION_CONFIRM", "Reservation", reservation_id)
    return booking_row(conn, reservation_id)


def reject(conn, actor: dict, reservation_id: int, reason: str) -> dict:
    b, p = _seller_booking(conn, actor, reservation_id)
    require(b["status"] == "PENDING" and p["status"] == "ON_SALE",
            "INVALID_STATUS", "只有待确认的在售预约可以拒绝", 409)
    reason = string(reason, "reason", limit=400)
    check_reservation_edge(b["status"], "CANCELLED")
    conn.execute(
        """UPDATE reservations SET status='CANCELLED',cancelled_at=?,
           cancel_source='SELLER_REJECTED',cancel_reason=? WHERE id=?""",
        (now(), reason, reservation_id),
    )
    log_success(conn, actor["id"], "RESERVATION_REJECT", "Reservation", reservation_id, reason)
    return booking_row(conn, reservation_id)


def cancel(conn, actor: dict, reservation_id: int) -> dict:
    require_normal(actor)
    b = booking_row(conn, reservation_id)
    require(b["buyer_id"] == actor["id"], "FORBIDDEN", "只能取消自己的预约", 403)
    require(b["status"] in {"PENDING", "CONFIRMED"},
            "INVALID_STATUS", "只有待确认或已确认预约可以取消", 409)
    check_reservation_edge(b["status"], "CANCELLED")
    conn.execute(
        """UPDATE reservations SET status='CANCELLED',cancelled_at=?,
           cancel_source='BUYER',cancel_reason='买家主动取消' WHERE id=?""",
        (now(), reservation_id),
    )
    log_success(conn, actor["id"], "RESERVATION_CANCEL", "Reservation", reservation_id)
    return booking_row(conn, reservation_id)


def complete_booking(conn, actor: dict, reservation_id: int) -> tuple[dict, int]:
    """M4-only transition; M2 marks the product sold within the same transaction."""
    b, p = _seller_booking(conn, actor, reservation_id)
    require(b["status"] == "CONFIRMED" and p["status"] == "ON_SALE",
            "INVALID_STATUS", "只有已确认且商品在售的预约可以完成", 409)
    check_reservation_edge(b["status"], "COMPLETED")
    conn.execute("UPDATE reservations SET status='COMPLETED',completed_at=? WHERE id=?",
                 (now(), reservation_id))
    return booking_row(conn, reservation_id), b["product_id"]


def cancel_for_delist(conn, product_id: int, *, violation: bool = False,
                      reason: str | None = None) -> int:
    """M4-only bulk cancellation of active bookings during product delisting."""
    source = "VIOLATION_DELISTED" if violation else "PRODUCT_DELISTED"
    message = reason if violation else "商品已由卖家下架"
    return conn.execute(
        """UPDATE reservations SET status='CANCELLED',cancelled_at=?,
           cancel_source=?,cancel_reason=?
           WHERE product_id=? AND status IN ('PENDING','CONFIRMED')""",
        (now(), source, message, product_id),
    ).rowcount
