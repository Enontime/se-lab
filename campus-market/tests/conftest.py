import pytest

from app.services.market import Market


@pytest.fixture
def setup_market(tmp_path):
    m = Market(tmp_path / "test.sqlite3")
    m.initialize()
    seller = m.create_account("seller", "SellerPwd123")
    buyer = m.create_account("buyer", "BuyerPwd123")
    buyer2 = m.create_account("buyer2", "Buyer2Pwd123")
    admin = m.bootstrap_admin("admin", "AdminPwd123")
    return m, seller, buyer, buyer2, admin


@pytest.fixture
def published_product(setup_market):
    m, seller, buyer, buyer2, admin = setup_market
    product = m.create_product(seller, {
        "title": "二手教材", "category": "书籍", "price_cents": 2600,
        "item_condition": "八成新", "description": "无缺页",
        "trade_location": "图书馆门口",
    })
    m.submit_product(seller, product["id"])
    p = m.review_product(admin, product["id"], "APPROVED")
    return m, seller, buyer, buyer2, admin, p
