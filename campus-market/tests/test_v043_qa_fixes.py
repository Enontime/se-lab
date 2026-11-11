"""v0.4.3 QA fixes: capacity, private review tabs, log actor lookup and UI traceability."""
from pathlib import Path
import pytest
from app.core.errors import BusinessError


def _item(m, seller, title):
    return m.create_product(seller, {"title": title})


def test_101_private_products_reachable_using_pagination(setup_market):
    market, seller, buyer, _, admin = setup_market
    for i in range(101):
        _item(market, seller, f"VOLUME-{i}")
    first = market.my_products(seller, limit=21, offset=0)
    second = market.my_products(seller, limit=21, offset=20)
    final = market.my_products(seller, limit=21, offset=100)
    assert len(first) == 21 and len(second) == 21 and len(final) == 1
    ids = [row["id"] for offset in range(0, 101, 20)
           for row in market.my_products(seller, limit=20, offset=offset)]
    assert len(ids) == len(set(ids)) == 101
    assert [row["title"] for row in final] == ["VOLUME-0"]
    assert market.my_products(buyer, limit=21, offset=0) == []


def test_reviewed_tabs_and_resubmission_exclusion(setup_market):
    market, seller, buyer, _, admin = setup_market
    p = market.create_product(seller, {"title": "待审核样本", "category": "书籍", "price_cents": 2000,
        "item_condition": "八成新", "description": "上学期教材", "trade_location": "图书馆"})
    market.submit_product(seller, p["id"])
    assert [x["id"] for x in market.review_queue(admin)] == [p["id"]]
    assert market.reviewed_products(admin, decision="REJECTED") == []
    market.review_product(admin, p["id"], "REJECTED", "补充书页情况")
    rejected = market.reviewed_products(admin, decision="REJECTED")
    assert [x["id"] for x in rejected] == [p["id"]]
    assert rejected[0]["review_reason"] == "补充书页情况"
    market.submit_product(seller, p["id"])
    assert market.reviewed_products(admin, decision="REJECTED") == []
    market.review_product(admin, p["id"], "APPROVED")
    assert [x["id"] for x in market.reviewed_products(admin, decision="APPROVED")] == [p["id"]]
    assert market.reviewed_products(admin, decision="APPROVED", offset=1) == []
    with pytest.raises(BusinessError) as err:
        market.reviewed_products(seller, decision="APPROVED")
    assert err.value.status == 403
    with pytest.raises(BusinessError):
        market.reviewed_products(admin, decision="UNKNOWN")


def test_log_actor_query_username_id_and_unknown(setup_market):
    market, seller, buyer, _, admin = setup_market
    _item(market, seller, "草稿")
    by_name = market.admin_logs(admin, actor_query="seller")
    by_id = market.admin_logs(admin, actor_query=str(seller["id"]))
    assert by_name and [x["id"] for x in by_id] == [x["id"] for x in by_name]
    assert by_name[0]["actor_name"] == "seller"
    assert market.admin_logs(admin, actor_query="not_a_user") == []
    assert market.admin_logs(admin, actor_query="9" * 60) == []
    with pytest.raises(BusinessError) as err:
        market.admin_logs(seller, actor_query="seller")
    assert err.value.status == 403


def test_frontend_all_five_reported_deviations_are_addressed_structurally():
    root = Path(__file__).resolve().parents[1]
    js = (root / "app/static/js/site.js").read_text(encoding="utf-8")
    css = (root / "app/static/css/site.css").read_text(encoding="utf-8")
    assert 'data-act="mine-next"' in js and 'data-act="mine-prev"' in js
    assert 'offset = state.mineOffset' in js and 'limit: pageSize + 1' in js
    assert '/admin/products/reviewed?decision=APPROVED' in js
    assert '/admin/products/reviewed?decision=REJECTED' in js
    assert 'data-act="review-next"' in js and 'data-act="review-prev"' in js
    assert 'name="category"' in js and 'reset-admin-stats' in js
    assert 'name="actor_query"' in js and 'name="action"' in js
    assert 'actionLabels[l.action]' in js and 'l.actor_name' in js
    assert 'stat("已下架"' in js and 'status-SOLD' in css
