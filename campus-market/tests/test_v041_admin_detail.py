"""v0.4.1 admin-only detail: unpublished records must stay private."""
from pathlib import Path

import pytest

from app.core.errors import BusinessError


def _draft(market, seller, title="二手数据库教材"):
    return market.create_product(seller, {
        "title": title, "category": "教材", "price_cents": 3800,
        "item_condition": "九成新", "description": "附课堂笔记",
        "trade_location": "图书馆南门",
    })


def test_admin_can_inspect_draft_and_pending_without_publication(setup_market):
    market, seller, buyer, _, admin = setup_market
    product = _draft(market, seller)
    pid = product["id"]
    detail = market.admin_product_detail(admin, pid)
    assert detail["status"] == "DRAFT"
    assert detail["seller_name"] == "seller"
    assert detail["description"] == "附课堂笔记"
    assert detail["trade_location"] == "图书馆南门"
    assert detail["price_cents"] == 3800
    assert detail["booked"] == 0
    with pytest.raises(BusinessError) as err:
        market.public_detail(pid)
    assert err.value.status == 404
    market.submit_product(seller, pid)
    assert market.admin_product_detail(admin, pid)["status"] == "PENDING_REVIEW"
    assert market.review_queue(admin)[0]["id"] == pid


def test_admin_detail_blocks_seller_and_other_users(setup_market):
    market, seller, buyer, other, admin = setup_market
    product = _draft(market, seller)
    for actor in (seller, buyer, other):
        with pytest.raises(BusinessError) as err:
            market.admin_product_detail(actor, product["id"])
        assert err.value.code == "FORBIDDEN"
        assert err.value.status == 403
    assert market.admin_product_detail(admin, product["id"])["id"] == product["id"]


def test_admin_detail_missing_or_invalid_id(setup_market):
    market, seller, buyer, other, admin = setup_market
    for product_id, expected_status in ((999999, 404), (0, 400)):
        with pytest.raises(BusinessError) as err:
            market.admin_product_detail(admin, product_id)
        assert err.value.status == expected_status


def test_admin_detail_preserves_state_and_view_after_review(setup_market):
    market, seller, buyer, _, admin = setup_market
    p = _draft(market, seller)
    market.submit_product(seller, p["id"])
    market.review_product(admin, p["id"], "REJECTED", "缺乏信息")
    assert market.admin_product_detail(admin, p["id"])["status"] == "REJECTED"
    assert market.review_history(admin, p["id"])[0]["reason"] == "缺乏信息"
    market.edit_product(seller, p["id"], {"description": "已补充真实描述"})
    market.submit_product(seller, p["id"])
    market.review_product(admin, p["id"], "APPROVED")
    detail = market.admin_product_detail(admin, p["id"])
    assert detail["status"] == "ON_SALE"
    assert detail["description"] == "已补充真实描述"
    # The new detail method is a read; it must not change state or create logs.
    assert market.admin_product_detail(admin, p["id"]) == detail


def test_ui_detail_is_a_page_and_history_is_secondary():
    root = Path(__file__).resolve().parents[1]
    js = (root / "app/static/js/site.js").read_text(encoding="utf-8")
    template = (root / "app/templates/index.html").read_text(encoding="utf-8")
    css = (root / "app/static/css/site.css").read_text(encoding="utf-8")
    assert 'href="#/admin-product/${p.id}"' in js
    assert 'async function adminProductPage(id, ctx = captureView())' in js
    assert 'api(`/admin/products/${id}`)' in js
    assert '历史审核记录' in js and '商品描述' in js and '交易地点' in js
    assert 'await showAdminProduct(id)' not in js
    assert 'class="brand-mark-glyph"' in template
    assert 'transform:translateY(-2px)' in css
