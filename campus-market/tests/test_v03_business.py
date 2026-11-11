"""v0.3: violation resolution, private read models, audit, migration and consistency."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.errors import BusinessError
from app.db.connection import connect, read_connection
from app.db.migrations import migration_status
from app.services.market import Market


def denied(fn, code: str):
    with pytest.raises(BusinessError) as exc:
        fn()
    assert exc.value.code == code


def post_product(m, seller, admin, **changes):
    fields = {"title": "旧教材", "category": "书籍", "price_cents": 2000,
              "item_condition": "九成新", "description": "无划线",
              "trade_location": "图书馆"}
    fields.update(changes)
    p = m.create_product(seller, fields)
    m.submit_product(seller, p["id"])
    return m.review_product(admin, p["id"], "APPROVED")


def book(m, buyer, p, request_id="v03-book"):
    return m.reserve(buyer, p["id"], request_id, p["version"], p["price_cents"])["reservation"]


def test_v03_violation_requires_admin_resolution_then_seller_review(published_product):
    m, seller, buyer, other, admin, p = published_product
    result = m.delist_product(admin, p["id"], violation=True, reason="违规信息")
    assert result["product"]["status"] == "VIOLATION_DELISTED"
    assert m.violation_queue(admin)[0]["reason"] == "违规信息"
    denied(lambda: m.submit_product(seller, p["id"]), "INVALID_STATUS")
    denied(lambda: m.resolve_violation(buyer, p["id"], "已整改"), "FORBIDDEN")
    denied(lambda: m.resolve_violation(admin, p["id"], " "), "INVALID_INPUT")
    done = m.resolve_violation(admin, p["id"], "违规内容已核查整改")
    assert done["case"]["resolved_at"] and done["case"]["resolved_by"] == admin["id"]
    assert done["product"]["status"] == "VIOLATION_DELISTED"
    denied(lambda: m.public_detail(p["id"]), "NOT_FOUND")
    denied(lambda: m.resolve_violation(admin, p["id"], "again"), "INVALID_STATUS")
    pending = m.submit_product(seller, p["id"])
    assert pending["status"] == "PENDING_REVIEW"
    denied(lambda: m.public_detail(p["id"]), "NOT_FOUND")
    approved = m.review_product(admin, p["id"], "APPROVED")
    assert approved["status"] == "ON_SALE"
    assert m.public_detail(p["id"])["id"] == p["id"]
    assert m.violation_queue(admin, status="OPEN") == []
    assert m.admin_logs(admin, action="PRODUCT_VIOLATION_RESOLVE")[0]["actor_id"] == admin["id"]


def test_v03_new_violation_creates_new_unresolved_case(published_product):
    m, seller, buyer, other, admin, p = published_product
    m.delist_product(admin, p["id"], violation=True, reason="第一次")
    m.resolve_violation(admin, p["id"], "第一次处理完毕")
    m.submit_product(seller, p["id"])
    m.review_product(admin, p["id"], "APPROVED")
    m.delist_product(admin, p["id"], violation=True, reason="第二次")
    cases = m.violation_history(seller, p["id"])
    assert len(cases) == 2
    assert cases[0]["reason"] == "第二次" and cases[0]["resolved_at"] is None
    assert cases[1]["reason"] == "第一次" and cases[1]["resolved_at"]
    denied(lambda: m.submit_product(seller, p["id"]), "INVALID_STATUS")
    assert m.violation_queue(admin, status="RESOLVED")[0]["reason"] == "第一次"
    assert m.violation_queue(admin, status="OPEN")[0]["reason"] == "第二次"


def test_v03_violation_resolve_is_atomic_on_audit_failure(monkeypatch, published_product):
    from app.services import audit
    m, seller, buyer, other, admin, p = published_product
    m.delist_product(admin, p["id"], violation=True, reason="不合规")
    def broken(*args, **kwargs):
        raise RuntimeError("injected audit failure")
    monkeypatch.setattr(audit, "log_success", broken)
    with pytest.raises(RuntimeError, match="injected audit"):
        m.resolve_violation(admin, p["id"], "已检查")
    with read_connection(m.db_path) as conn:
        assert conn.execute("SELECT resolved_at FROM violation_cases").fetchone()[0] is None


def test_v03_violation_cancel_reservation_atomicity(published_product):
    m, seller, buyer, other, admin, p = published_product
    b = book(m, buyer, p)
    response = m.delist_product(admin, p["id"], violation=True, reason="违规")
    assert response["cancelled_reservations"] == 1
    assert m.private_reservation(buyer, b["id"])["status"] == "CANCELLED"
    assert m.private_reservation(buyer, b["id"])["cancel_reason"] == "违规"
    assert m.integrity_report(admin)["ok"]


def test_v03_private_products_reservations_and_dashboard(published_product):
    m, seller, buyer, other, admin, p = published_product
    assert m.my_product_detail(seller, p["id"])["id"] == p["id"]
    denied(lambda: m.my_product_detail(buyer, p["id"]), "NOT_FOUND")
    denied(lambda: m.my_product_detail(admin, p["id"]), "FORBIDDEN")
    b = book(m, buyer, p)
    assert m.private_reservation(buyer, b["id"])["counterparty_name"] == seller["username"]
    assert m.private_reservation(seller, b["id"])["counterparty_name"] == buyer["username"]
    denied(lambda: m.private_reservation(other, b["id"]), "NOT_FOUND")
    denied(lambda: m.private_reservation(admin, b["id"]), "FORBIDDEN")
    assert m.my_dashboard(seller)["products"]["by_status"]["ON_SALE"] == 1
    assert m.my_dashboard(buyer)["reservations"]["as_buyer"]["by_status"]["PENDING"] == 1
    assert m.my_reservations(buyer, status="PENDING")[0]["id"] == b["id"]
    assert m.received_reservations(seller, status="CONFIRMED") == []
    denied(lambda: m.my_products(seller, status="UNDEFINED"), "INVALID_STATUS_FILTER")
    denied(lambda: m.my_products(seller, limit=101), "INVALID_INPUT")


def test_v03_review_history_scoping(published_product):
    m, seller, buyer, other, admin, p = published_product
    history = m.review_history(seller, p["id"])
    assert len(history) == 1 and history[0]["decision"] == "APPROVED"
    assert history == m.review_history(admin, p["id"])
    denied(lambda: m.review_history(buyer, p["id"]), "NOT_FOUND")
    denied(lambda: m.violation_history(buyer, p["id"]), "FORBIDDEN")


def test_v03_admin_review_worklist_and_previous_review_count(setup_market):
    m, seller, buyer, other, admin = setup_market
    raw = m.create_product(seller, {"title": "未提交"})
    rejected = m.create_product(seller, {"title": "修订", "category": "书籍",
        "price_cents": 3000, "item_condition": "新", "description": "无", "trade_location": "图书馆"})
    m.submit_product(seller, rejected["id"])
    m.review_product(admin, rejected["id"], "REJECTED", "描述不准确")
    m.submit_product(seller, rejected["id"])
    pending = m.review_queue(admin, keyword="修订")
    assert len(pending) == 1 and pending[0]["previous_reviews"] == 1
    assert m.review_workload(admin)["PENDING_REVIEW"] == 1
    denied(lambda: m.review_queue(buyer), "FORBIDDEN")
    assert m.review_queue(admin, keyword="nope") == []
    m.review_product(admin, rejected["id"], "APPROVED")
    assert m.review_queue(admin) == []
    assert m.my_product_detail(seller, raw["id"])["status"] == "DRAFT"


def test_v03_literal_search_wildcards_and_public_facets(setup_market):
    m, seller, buyer, other, admin = setup_market
    percent = post_product(m, seller, admin, title="100% 棉")
    underline = post_product(m, seller, admin, title="书_有痕")
    hidden = m.create_product(seller, {"title": "未公开%"})
    assert len(m.list_public(keyword="%")) == 1
    assert m.list_public(keyword="%")[0]["id"] == percent["id"]
    assert m.list_public(keyword="_")[0]["id"] == underline["id"]
    assert m.public_count()["count"] == 2
    assert m.public_facets()["available"] == 2
    b = book(m, buyer, percent)
    assert m.public_count(availability="RESERVED")["count"] == 1
    assert m.public_facets()["reserved"] == 1
    m.cancel_reservation(buyer, b["id"])
    assert m.public_count(availability="AVAILABLE")["count"] == 2
    denied(lambda: m.public_count(availability="UNKNOWN"), "INVALID_INPUT")
    assert hidden["id"] not in [p["id"] for p in m.list_public()]


def test_v03_request_snapshot_and_integrity_checker(published_product):
    m, seller, buyer, other, admin, p = published_product
    b = book(m, buyer, p)
    assert b["expected_version"] == p["version"]
    assert b["expected_price_cents"] == p["price_cents"]
    assert m.integrity_report(admin)["ok"]
    denied(lambda: m.integrity_report(buyer), "FORBIDDEN")
    with connect(m.db_path) as conn:
        conn.execute("UPDATE reservations SET expected_price_cents=12345 WHERE id=?", (b["id"],))
    issue = m.integrity_report(admin)
    assert not issue["ok"]
    assert any(item["code"] == "REQUEST_SNAPSHOT_MISMATCH" for item in issue["issues"])


def test_v03_integrity_checker_finds_inconsistent_sold_product(published_product):
    m, seller, buyer, other, admin, p = published_product
    with connect(m.db_path) as conn:
        conn.execute("UPDATE products SET status='SOLD' WHERE id=?", (p["id"],))
    report = m.integrity_report(admin)
    assert not report["ok"]
    assert {x["code"] for x in report["issues"]} >= {"SOLD_WITHOUT_COMPLETED"}
    assert m.product_diagnostics(admin, p["id"])["invariants_ok"] is False
    denied(lambda: m.product_diagnostics(buyer, p["id"]), "FORBIDDEN")


def test_v03_log_target_filters_and_admin_activity(published_product):
    m, seller, buyer, other, admin, p = published_product
    b = book(m, buyer, p)
    events = m.admin_product_timeline(admin, p["id"])
    actions = {x["action"] for x in events}
    assert {"RESERVATION_CREATE", "PRODUCT_REVIEW_APPROVED"} <= actions
    assert m.admin_logs(admin, target_type="Reservation", target_id=b["id"])[0]["action"] == "RESERVATION_CREATE"
    assert m.admin_activity_summary(admin)["total"] >= 3
    assert m.admin_user_activity(admin, buyer["id"])["events"][0]["actor_id"] == buyer["id"]
    denied(lambda: m.admin_product_timeline(buyer, p["id"]), "FORBIDDEN")
    denied(lambda: m.admin_activity_summary(buyer), "FORBIDDEN")
    denied(lambda: m.admin_user_activity(buyer, seller["id"]), "FORBIDDEN")


def test_v03_migration_from_old_product_violation_data(tmp_path):
    db = tmp_path / "upgrade.sqlite3"
    m = Market(db)
    m.initialize()
    seller = m.create_account("legacySeller", "12345678")
    admin = m.bootstrap_admin("legacyAdmin", "12345678")
    p = post_product(m, seller, admin)
    m.delist_product(admin, p["id"], violation=True, reason="old case")
    # Emulate an older schema that had neither support table nor snapshot fields.
    with connect(db) as conn:
        conn.execute("DROP TABLE violation_cases")
        conn.execute("ALTER TABLE reservations DROP COLUMN expected_version")
        conn.execute("ALTER TABLE reservations DROP COLUMN expected_price_cents")
    m.initialize()
    assert migration_status(connect(db))["untracked_violations"] == 0
    assert m.violation_queue(admin)[0]["reason"] == "old case"
    assert m.violation_queue(admin)[0]["flagged_by"] == admin["id"]
    with read_connection(db) as conn:
        names = {row["name"] for row in conn.execute("PRAGMA table_info(reservations)")}
        assert {"expected_version", "expected_price_cents"} <= names
    m.initialize()  # Repeated startup must not duplicate cases.
    assert len(m.violation_history(seller, p["id"])) == 1


def test_v03_invalid_date_in_platform_stats(published_product):
    m, seller, buyer, other, admin, p = published_product
    denied(lambda: m.admin_stats(admin, start="2026-02-30"), "INVALID_DATE")
    denied(lambda: m.admin_activity_summary(admin, start="2026-11-11", end="2026-11-10"), "INVALID_RANGE")
    denied(lambda: m.admin_activity_summary(admin, start="bad"), "INVALID_DATE")
