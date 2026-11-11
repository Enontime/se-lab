"""REQ-005 V1.2 and QA-043 regression: separate state transition and page reachability."""
from pathlib import Path

import pytest

from app.core.errors import BusinessError
from app.core.state_rules import PRODUCT_GRAPH, valid_product_edge
from app.db.connection import read_connection

FIELD_VALUES = {
    "title": "更新商品名", "category": "电子设备", "price_cents": 1,
    "item_condition": "全新", "description": "更新说明", "trade_location": "新交易地点",
}


def _publish(market, seller, admin):
    p = market.create_product(seller, {
        "title": "旧教材", "category": "书籍", "price_cents": 2800,
        "item_condition": "九成新", "description": "旧描述", "trade_location": "图书馆",
    })
    market.submit_product(seller, p["id"])
    return market.review_product(admin, p["id"], "APPROVED")


def _error(fn, name, status=None):
    with pytest.raises(BusinessError) as ex:
        fn()
    assert ex.value.code == name
    if status is not None:
        assert ex.value.status == status


def test_v12_state_graph_only_supports_separate_edit_and_submit():
    assert valid_product_edge("ON_SALE", "DRAFT")
    assert not valid_product_edge("ON_SALE", "PENDING_REVIEW")
    assert valid_product_edge("DRAFT", "PENDING_REVIEW")
    assert set(PRODUCT_GRAPH) == {
        "DRAFT", "PENDING_REVIEW", "REJECTED", "ON_SALE", "DELISTED",
        "VIOLATION_DELISTED", "SOLD",
    }


@pytest.mark.parametrize("field,value", list(FIELD_VALUES.items()))
def test_v12_each_of_six_fields_requires_two_step_review(setup_market, field, value):
    market, seller, buyer, _, admin = setup_market
    old = _publish(market, seller, admin)
    changed = market.edit_product(seller, old["id"], {field: value})
    assert changed[field] == value
    assert changed["status"] == "DRAFT"
    assert changed["version"] == old["version"] + 1
    assert changed["published_at"] == old["published_at"]
    assert market.list_public() == []
    assert market.my_products(seller)[0]["status"] == "DRAFT"
    _error(lambda: market.public_detail(old["id"]), "NOT_FOUND", 404)
    _error(lambda: market.review_product(admin, old["id"], "APPROVED"), "INVALID_STATUS")
    assert not market.review_queue(admin)
    pending = market.submit_product(seller, old["id"])
    assert pending["status"] == "PENDING_REVIEW"
    assert market.list_public() == []
    approved = market.review_product(admin, old["id"], "APPROVED")
    assert approved["status"] == "ON_SALE"
    assert approved[field] == value
    assert approved["published_at"] == old["published_at"]
    _error(lambda: market.reserve(buyer, old["id"], "stale", old["version"], old["price_cents"]), "STALE_PRODUCT")
    assert market.public_detail(old["id"])["seller_name"] == "seller"
    with read_connection(market.db_path) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM operation_logs WHERE target_type='Product' AND target_id=? ORDER BY id", (old["id"],))]
        assert actions.count("PRODUCT_EDIT_TO_DRAFT") == 1
        assert actions.count("PRODUCT_SUBMIT") == 2
        assert actions.count("PRODUCT_REVIEW_APPROVED") == 2


@pytest.mark.parametrize("confirmed", [False, True])
def test_v12_active_booking_blocks_all_edit_fields(setup_market, confirmed):
    market, seller, buyer, _, admin = setup_market
    p = _publish(market, seller, admin)
    rid = market.reserve(buyer, p["id"], "booking", p["version"], p["price_cents"])["reservation"]["id"]
    if confirmed:
        market.confirm_reservation(seller, rid)
    for field, value in FIELD_VALUES.items():
        _error(lambda field=field, value=value: market.edit_product(seller, p["id"], {field: value}), "HAS_BOOKING", 409)
    assert market.public_detail(p["id"])["status"] == "ON_SALE"
    market.cancel_reservation(buyer, rid)
    assert market.edit_product(seller, p["id"], {"description": "取消后可修改"})["status"] == "DRAFT"


def test_v12_noop_rejected_without_unpublishing(setup_market):
    market, seller, buyer, _, admin = setup_market
    p = _publish(market, seller, admin)
    before = market.my_product_detail(seller, p["id"])
    _error(lambda: market.edit_product(seller, p["id"], {
        "title": p["title"], "category": p["category"], "price_cents": p["price_cents"],
        "item_condition": p["item_condition"], "description": p["description"],
        "trade_location": p["trade_location"],
    }), "NO_CHANGES", 400)
    assert market.my_product_detail(seller, p["id"]) == before
    assert market.public_detail(p["id"])["id"] == p["id"]


def test_v12_forbidden_invalid_and_sold_still_rejected(setup_market):
    market, seller, buyer, other, admin = setup_market
    p = _publish(market, seller, admin)
    _error(lambda: market.edit_product(buyer, p["id"], {"description": "恶意修改"}), "FORBIDDEN", 403)
    _error(lambda: market.edit_product(seller, p["id"], {"category": ""}), "INVALID_INPUT")
    _error(lambda: market.edit_product(seller, p["id"], {"price_cents": -1}), "INVALID_INPUT")
    b = market.reserve(buyer, p["id"], "last", p["version"], p["price_cents"])["reservation"]
    market.confirm_reservation(seller, b["id"])
    market.complete_reservation(seller, b["id"])
    _error(lambda: market.edit_product(seller, p["id"], {"title": "改售出"}), "INVALID_STATUS", 409)


def test_v12_six_fields_atomic_and_log_failure_rolls_back(setup_market, monkeypatch):
    market, seller, buyer, _, admin = setup_market
    p = _publish(market, seller, admin)
    import app.services.products as products
    before = market.my_product_detail(seller, p["id"])
    def fail(*args):
        raise RuntimeError("audit forced failure")
    with monkeypatch.context() as patch:
        patch.setattr(products, "log_success", fail)
        with pytest.raises(RuntimeError):
            market.edit_product(seller, p["id"], FIELD_VALUES)
    assert market.my_product_detail(seller, p["id"]) == before
    assert market.public_detail(p["id"])["id"] == p["id"]
    updated = market.edit_product(seller, p["id"], FIELD_VALUES)
    assert all(updated[field] == val for field, val in FIELD_VALUES.items())


def test_qa043_101_buyer_and_seller_bookings_paginated(setup_market):
    market, seller, buyer, other, admin = setup_market
    p = _publish(market, seller, admin)
    for i in range(101):
        rid = market.reserve(buyer, p["id"], f"page-{i}", p["version"], p["price_cents"])["reservation"]["id"]
        market.cancel_reservation(buyer, rid)
    for side in (lambda offset: market.my_reservations(buyer, limit=21, offset=offset),
                 lambda offset: market.received_reservations(seller, limit=21, offset=offset)):
        assert len(side(0)) == len(side(20)) == 21
        assert len(side(100)) == 1
        ids = [x["id"] for offset in range(0, 101, 20)
               for x in side(offset)[:20]]
        assert len(ids) == len(set(ids)) == 101
    assert market.my_reservations(other) == []
    assert market.received_reservations(other) == []


def test_qa043_101_admin_logs_paginated_and_private(setup_market):
    market, seller, buyer, other, admin = setup_market
    for i in range(101):
        market.create_product(seller, {"title": f"LOG-{i}"})
    logs = market.admin_logs(admin, action="PRODUCT_DRAFT_CREATE", limit=21)
    assert len(logs) == 21
    assert len(market.admin_logs(admin, action="PRODUCT_DRAFT_CREATE", offset=100, limit=21)) == 1
    ids = [x["id"] for offset in range(0, 101, 20)
           for x in market.admin_logs(admin, action="PRODUCT_DRAFT_CREATE", limit=20, offset=offset)]
    assert len(ids) == len(set(ids)) == 101
    _error(lambda: market.admin_logs(seller), "FORBIDDEN", 403)


def test_qa043_public_seller_name_safe_and_private_detail_hidden(setup_market):
    market, seller, buyer, other, admin = setup_market
    p = _publish(market, seller, admin)
    detail = market.public_detail(p["id"])
    assert detail["seller_name"] == "seller"
    assert "password_hash" not in detail and "token" not in detail
    market.edit_product(seller, p["id"], {"title": "撤回"})
    _error(lambda: market.public_detail(p["id"]), "NOT_FOUND", 404)
    assert market.my_product_detail(seller, p["id"])["title"] == "撤回"


def test_v044_frontend_page_and_req005_contract():
    js = (Path(__file__).resolve().parents[1] / "app/static/js/site.js").read_text("utf8")
    for key in ("bookings", "received"):
        assert f'data-act="${{key}}-prev"' in js
        assert f'data-act="${{key}}-next"' in js
    for action in ('logs-prev', 'logs-next'):
        assert f'data-act="{action}"' in js
    assert 'pageSize + 1, offset' in js
    assert 'state.logOffset = 0' in js
    assert 'state.bookingOffsets[el.dataset.tab] = 0' in js
    assert '保存修改并转为草稿' in js
    assert 'product.seller_name' in js
    assert '"ON_SALE", "PENDING_REVIEW"' not in js  # no accidental hardcoded edge
