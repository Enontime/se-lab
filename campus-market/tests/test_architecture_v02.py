"""v0.2 regression tests: explicit cross-domain orchestration and M6 queries."""
from datetime import datetime, timezone

import pytest

from app.core.errors import BusinessError
from app.db.connection import read_connection
from app.services import audit


def _book(market, buyer, product, request_id="v02-a"):
    return market.reserve(buyer, product["id"], request_id,
                          product["version"], product["price_cents"])["reservation"]


def test_delist_coordinates_m2_m4_m6(published_product):
    market, seller, buyer, other, admin, product = published_product
    booking = _book(market, buyer, product)
    result = market.delist_product(seller, product["id"])
    assert result["product"]["status"] == "DELISTED"
    assert result["cancelled_reservations"] == 1
    with read_connection(market.db_path) as conn:
        row = conn.execute("SELECT * FROM reservations WHERE id=?", (booking["id"],)).fetchone()
        assert row["status"] == "CANCELLED"
        assert row["cancel_source"] == "PRODUCT_DELISTED"
        actions = [x[0] for x in conn.execute(
            "SELECT action FROM operation_logs WHERE target_id=? ORDER BY id DESC",
            (product["id"],))]
        assert "PRODUCT_DELIST" in actions


def test_complete_coordinates_m4_m2_m6(published_product):
    market, seller, buyer, other, admin, product = published_product
    booking = _book(market, buyer, product)
    market.confirm_reservation(seller, booking["id"])
    result = market.complete_reservation(seller, booking["id"])
    assert result["reservation"]["status"] == "COMPLETED"
    assert result["product"]["status"] == "SOLD"
    assert market.admin_logs(admin, action="RESERVATION_COMPLETE")[0]["target_id"] == booking["id"]


def test_delist_log_failure_rolls_back_all_updates(monkeypatch, published_product):
    market, seller, buyer, other, admin, product = published_product
    booking = _book(market, buyer, product)

    def fail_log(*args, **kwargs):
        raise RuntimeError("injected log failure")

    monkeypatch.setattr(audit, "log_success", fail_log)
    with pytest.raises(RuntimeError, match="injected"):
        market.delist_product(seller, product["id"])
    with read_connection(market.db_path) as conn:
        assert conn.execute("SELECT status FROM products WHERE id=?", (product["id"],)).fetchone()[0] == "ON_SALE"
        assert conn.execute("SELECT status FROM reservations WHERE id=?", (booking["id"],)).fetchone()[0] == "PENDING"


def test_completion_log_failure_rolls_back_both_states(monkeypatch, published_product):
    market, seller, buyer, other, admin, product = published_product
    booking = _book(market, buyer, product)
    market.confirm_reservation(seller, booking["id"])

    def fail_log(*args, **kwargs):
        raise RuntimeError("injected log failure")

    monkeypatch.setattr(audit, "log_success", fail_log)
    with pytest.raises(RuntimeError, match="injected"):
        market.complete_reservation(seller, booking["id"])
    with read_connection(market.db_path) as conn:
        assert conn.execute("SELECT status FROM products WHERE id=?", (product["id"],)).fetchone()[0] == "ON_SALE"
        assert conn.execute("SELECT status FROM reservations WHERE id=?", (booking["id"],)).fetchone()[0] == "CONFIRMED"


def test_log_filters_and_role_access(published_product):
    market, seller, buyer, other, admin, product = published_product
    _book(market, buyer, product)
    all_logs = market.admin_logs(admin)
    assert len(all_logs) >= 4
    subset = market.admin_logs(admin, action="RESERVATION_CREATE", actor_id=buyer["id"])
    assert len(subset) == 1
    assert subset[0]["actor_id"] == buyer["id"]
    assert subset[0]["action"] == "RESERVATION_CREATE"
    assert market.admin_logs(admin, action="SOMETHING_NOT_PRESENT") == []
    assert market.admin_logs(admin, limit=1, offset=1)[0]["id"] == all_logs[1]["id"]
    today = datetime.now(timezone.utc).date().isoformat()
    assert market.admin_logs(admin, start="2000-01-01", end="2999-12-31")
    for kwargs in ({"start": "2026-99-99"}, {"start": "2026-01-02", "end": "2026-01-01"},
                   {"limit": 101}, {"offset": -1}):
        with pytest.raises(BusinessError):
            market.admin_logs(admin, **kwargs)
    with pytest.raises(BusinessError) as err:
        market.admin_logs(buyer)
    assert err.value.code == "FORBIDDEN"
