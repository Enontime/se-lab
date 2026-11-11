import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.core.errors import BusinessError
from app.db.connection import read_connection
from app.services.market import Market


def error_code(fn):
    with pytest.raises(BusinessError) as exc:
        fn()
    return exc.value.code


def book(m, buyer, p, req):
    return m.reserve(buyer, p["id"], req, p["version"], p["price_cents"])


def test_account_security_and_admin_bootstrap(setup_market):
    m, seller, buyer, buyer2, admin = setup_market
    assert error_code(lambda: m.create_account("seller", "OtherPwd123")) == "USERNAME_EXISTS"
    assert error_code(lambda: m.login("buyer", "wrong-password")) == "BAD_CREDENTIALS"
    session = m.login("buyer", "BuyerPwd123")
    assert m.identity(session["token"])["account_type"] == "NORMAL"
    m.logout(session["token"])
    assert error_code(lambda: m.identity(session["token"])) == "UNAUTHORIZED"
    assert error_code(lambda: m.create_product(admin, {"title": "x"})) == "FORBIDDEN"


def test_draft_review_publish_and_private_visibility(setup_market):
    m, seller, buyer, buyer2, admin = setup_market
    draft = m.create_product(seller, {"title": "教材"})
    assert m.list_public() == []
    assert error_code(lambda: m.submit_product(seller, draft["id"])) == "INCOMPLETE_PRODUCT"
    m.edit_product(seller, draft["id"], {
        "category": "教材", "price_cents": 0, "item_condition": "全新",
        "description": "说明", "trade_location": "食堂"})
    assert error_code(lambda: m.submit_product(buyer, draft["id"])) == "FORBIDDEN"
    m.submit_product(seller, draft["id"])
    assert m.list_public() == []
    assert error_code(lambda: m.review_product(buyer, draft["id"], "APPROVED")) == "FORBIDDEN"
    approved = m.review_product(admin, draft["id"], "APPROVED")
    assert approved["status"] == "ON_SALE"
    assert len(m.list_public()) == 1
    assert m.public_detail(draft["id"])["booked"] is False


def test_reservation_idempotency_and_new_request(published_product):
    m, seller, buyer, buyer2, admin, p = published_product
    first = book(m, buyer, p, "request-001")
    assert first["idempotent_replay"] is False
    assert first["reservation"]["status"] == "PENDING"
    again = book(m, buyer, p, "request-001")
    assert again["idempotent_replay"] is True
    assert again["reservation"]["id"] == first["reservation"]["id"]
    assert error_code(lambda: m.reserve(buyer, p["id"], "request-001", p["version"] + 1, p["price_cents"])) == "REQUEST_REUSED"
    assert error_code(lambda: book(m, buyer2, p, "request-002")) == "PRODUCT_RESERVED"
    assert error_code(lambda: book(m, seller, p, "own")) == "OWN_PRODUCT"
    m.cancel_reservation(buyer, first["reservation"]["id"])
    replay = book(m, buyer, p, "request-001")
    assert replay["reservation"]["status"] == "CANCELLED"
    assert replay["idempotent_replay"] is True
    next_book = book(m, buyer2, p, "request-003")
    assert next_book["reservation"]["id"] != first["reservation"]["id"]
    with read_connection(m.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM reservations WHERE product_id=? AND status IN ('PENDING','CONFIRMED')", (p["id"],)).fetchone()[0]
        assert count == 1


def test_confirm_complete_atomic_states(published_product):
    m, seller, buyer, buyer2, admin, p = published_product
    rid = book(m, buyer, p, "complete") ["reservation"]["id"]
    assert error_code(lambda: m.complete_reservation(seller, rid)) == "INVALID_STATUS"
    m.confirm_reservation(seller, rid)
    done = m.complete_reservation(seller, rid)
    assert done["reservation"]["status"] == "COMPLETED"
    assert done["product"]["status"] == "SOLD"
    assert error_code(lambda: m.cancel_reservation(buyer, rid)) == "INVALID_STATUS"
    assert error_code(lambda: book(m, buyer2, p, "later")) == "NOT_ON_SALE"


def test_delist_cancels_booking_without_resurrecting(published_product):
    m, seller, buyer, buyer2, admin, p = published_product
    rid = book(m, buyer, p, "to-delist")["reservation"]["id"]
    m.confirm_reservation(seller, rid)
    result = m.delist_product(seller, p["id"])
    assert result["product"]["status"] == "DELISTED"
    assert result["cancelled_reservations"] == 1
    reservation = m.my_reservations(buyer)[0]
    assert reservation["status"] == "CANCELLED"
    assert reservation["cancel_source"] == "PRODUCT_DELISTED"
    assert error_code(lambda: m.cancel_reservation(buyer, rid)) == "INVALID_STATUS"
    assert error_code(lambda: book(m, buyer2, p, "after-delist")) == "NOT_ON_SALE"


def test_violation_delist_and_access(published_product):
    m, seller, buyer, buyer2, admin, p = published_product
    assert error_code(lambda: m.delist_product(buyer, p["id"])) == "FORBIDDEN"
    assert error_code(lambda: m.delist_product(seller, p["id"], violation=True,
                                             reason="违规")) == "FORBIDDEN"
    result = m.delist_product(admin, p["id"], violation=True, reason="违规商品")
    assert result["product"]["status"] == "VIOLATION_DELISTED"
    assert result["product"]["violation_reason"] == "违规商品"
    assert error_code(lambda: m.submit_product(seller, p["id"])) == "INVALID_STATUS"


def test_key_field_edit_requires_review_and_stale_page(published_product):
    m, seller, buyer, buyer2, admin, p = published_product
    changed = m.edit_product(seller, p["id"], {"price_cents": 3300})
    assert changed["status"] == "DRAFT"
    assert m.list_public() == []
    assert error_code(lambda: book(m, buyer, p, "stale")) == "NOT_ON_SALE"
    assert m.submit_product(seller, p["id"])["status"] == "PENDING_REVIEW"
    m.review_product(admin, p["id"], "APPROVED")
    assert error_code(lambda: book(m, buyer, p, "old-page")) == "STALE_PRODUCT"


def test_logs_coupled_to_reservation_transaction(published_product, monkeypatch):
    m, seller, buyer, buyer2, admin, p = published_product
    import app.services.reservations as reservations

    def failed_log(*args, **kwargs):
        raise RuntimeError("simulated audit insert failure")

    with monkeypatch.context() as patch:
        patch.setattr(reservations, "log_success", failed_log)
        with pytest.raises(RuntimeError):
            book(m, buyer, p, "rollback")
    with read_connection(m.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 0
    assert book(m, buyer, p, "rollback")["reservation"]["status"] == "PENDING"
    assert any(log["action"] == "RESERVATION_CREATE" for log in m.admin_logs(admin))


def test_two_buyers_compete_twenty_groups(setup_market):
    m, seller, buyer, buyer2, admin = setup_market
    for k in range(20):
        p = m.create_product(seller, {
            "title": f"item{k}", "category": "其他", "price_cents": 50,
            "item_condition": "全新", "description": "说明", "trade_location": "教室"})
        m.submit_product(seller, p["id"])
        p = m.review_product(admin, p["id"], "APPROVED")
        barrier = Barrier(2)

        def attempt(actor, req):
            barrier.wait()
            try:
                return book(m, actor, p, req)["reservation"]["id"]
            except BusinessError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(attempt, buyer, f"b1-{k}")
            f2 = pool.submit(attempt, buyer2, f"b2-{k}")
            outcomes = [f1.result(), f2.result()]
        assert sum(type(x) is int for x in outcomes) == 1, outcomes
        assert "PRODUCT_RESERVED" in outcomes
    with read_connection(m.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 20
        assert conn.execute("""SELECT COUNT(*) FROM (
           SELECT product_id FROM reservations WHERE status IN ('PENDING','CONFIRMED')
           GROUP BY product_id HAVING COUNT(*)>1)""").fetchone()[0] == 0


def test_reservation_and_delist_race(published_product):
    m, seller, buyer, buyer2, admin, p = published_product
    barrier = Barrier(2)

    def booking():
        barrier.wait()
        try:
            return book(m, buyer, p, "race")["reservation"]["status"]
        except BusinessError as exc:
            return exc.code

    def delisting():
        barrier.wait()
        return m.delist_product(seller, p["id"])["product"]["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(booking)
        f2 = pool.submit(delisting)
        result, state = f1.result(), f2.result()
    assert state == "DELISTED"
    assert result in {"PENDING", "NOT_ON_SALE"}
    with read_connection(m.db_path) as conn:
        assert conn.execute("""SELECT COUNT(*) FROM reservations
            WHERE product_id=? AND status IN ('PENDING','CONFIRMED')""",
            (p["id"],)).fetchone()[0] == 0


def test_catalog_and_statistics(published_product):
    m, seller, buyer, buyer2, admin, p = published_product
    assert len(m.list_public(keyword="二手", category="书籍", max_price=3000)) == 1
    assert m.list_public(category="数码") == []
    assert error_code(lambda: m.list_public(min_price=9999, max_price=2)) == "INVALID_RANGE"
    b = book(m, buyer, p, "test-stats")
    assert m.list_public(availability="AVAILABLE") == []
    assert len(m.list_public(availability="RESERVED")) == 1
    m.confirm_reservation(seller, b["reservation"]["id"])
    m.complete_reservation(seller, b["reservation"]["id"])
    assert m.seller_stats(seller)["completed_transactions"] == 1
    stats = m.admin_stats(admin)
    assert len(stats["published"]) == 1
    assert len(stats["completed"]) == 1
    assert error_code(lambda: m.admin_logs(buyer)) == "FORBIDDEN"
