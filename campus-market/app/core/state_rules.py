"""State policy: original UML U4/U5 plus the confirmed REQ-005 V1.2 change.

The graph describes *permitted edges*. Identity, ownership, transaction and
precondition checks are still required by the calling business service.
"""
from __future__ import annotations

from app.core.errors import require

PRODUCT_GRAPH = {
    "DRAFT": frozenset({"PENDING_REVIEW"}),
    "PENDING_REVIEW": frozenset({"ON_SALE", "REJECTED"}),
    "REJECTED": frozenset({"PENDING_REVIEW"}),
    "ON_SALE": frozenset({"DRAFT", "DELISTED", "VIOLATION_DELISTED", "SOLD"}),
    "DELISTED": frozenset({"PENDING_REVIEW"}),
    "VIOLATION_DELISTED": frozenset({"PENDING_REVIEW"}),
    "SOLD": frozenset(),
}
RESERVATION_GRAPH = {
    "PENDING": frozenset({"CONFIRMED", "CANCELLED"}),
    "CONFIRMED": frozenset({"CANCELLED", "COMPLETED"}),
    "CANCELLED": frozenset(),
    "COMPLETED": frozenset(),
}
PRODUCT_LABELS = {
    "DRAFT": "草稿", "PENDING_REVIEW": "待审核", "REJECTED": "审核驳回",
    "ON_SALE": "在售", "DELISTED": "已下架", "VIOLATION_DELISTED": "违规下架",
    "SOLD": "已售出",
}
RESERVATION_LABELS = {
    "PENDING": "待确认", "CONFIRMED": "已确认",
    "CANCELLED": "已取消", "COMPLETED": "已完成",
}
ACTIVE_RESERVATIONS = frozenset({"PENDING", "CONFIRMED"})
TERMINAL_PRODUCTS = frozenset({"SOLD"})
TERMINAL_RESERVATIONS = frozenset({"CANCELLED", "COMPLETED"})


def check_product_edge(before: str, after: str) -> None:
    """Raise INVALID_STATUS for an edge not drawn in UML U4."""
    require(before in PRODUCT_GRAPH and after in PRODUCT_GRAPH,
            "INVALID_STATUS", "未知商品状态", 409)
    require(after in PRODUCT_GRAPH[before], "INVALID_STATUS",
            f"商品不能由 {PRODUCT_LABELS[before]} 转换为 {PRODUCT_LABELS[after]}", 409)


def check_reservation_edge(before: str, after: str) -> None:
    """Raise INVALID_STATUS for an edge not drawn in UML U5."""
    require(before in RESERVATION_GRAPH and after in RESERVATION_GRAPH,
            "INVALID_STATUS", "未知预约状态", 409)
    require(after in RESERVATION_GRAPH[before], "INVALID_STATUS",
            f"预约不能由 {RESERVATION_LABELS[before]} 转换为 {RESERVATION_LABELS[after]}", 409)


def valid_product_edge(before: str, after: str) -> bool:
    return after in PRODUCT_GRAPH.get(before, ())


def valid_reservation_edge(before: str, after: str) -> bool:
    return after in RESERVATION_GRAPH.get(before, ())


def can_book(product_status: str, active_booking: bool) -> bool:
    return product_status == "ON_SALE" and not active_booking


def can_complete(product_status: str, reservation_status: str) -> bool:
    return product_status == "ON_SALE" and reservation_status == "CONFIRMED"


def can_cancel(reservation_status: str) -> bool:
    return reservation_status in ACTIVE_RESERVATIONS


def can_resubmit(product_status: str, *, violation_resolved: bool = False) -> bool:
    if product_status == "VIOLATION_DELISTED":
        return violation_resolved
    return product_status in {"DRAFT", "REJECTED", "DELISTED"}


def state_catalog() -> dict:
    """Small client-readable state spec; server remains authoritative."""
    return {
        "product": {"labels": PRODUCT_LABELS,
                    "transitions": {s: sorted(next_states)
                                    for s, next_states in PRODUCT_GRAPH.items()},
                    "terminal": sorted(TERMINAL_PRODUCTS)},
        "reservation": {"labels": RESERVATION_LABELS,
                        "transitions": {s: sorted(next_states)
                                        for s, next_states in RESERVATION_GRAPH.items()},
                        "terminal": sorted(TERMINAL_RESERVATIONS),
                        "active": sorted(ACTIVE_RESERVATIONS)},
    }
