"""A full in-memory-demo-like flow on a temporary on-disk SQLite database."""
from __future__ import annotations

from tempfile import TemporaryDirectory
from pathlib import Path

from app.services.market import Market


def main():
    with TemporaryDirectory() as tmp:
        market = Market(Path(tmp) / "demo.sqlite3")
        market.initialize()
        seller = market.create_account("demo_seller", "SellerPassword123")
        buyer = market.create_account("demo_buyer", "BuyerPassword123")
        admin = market.bootstrap_admin("demo_admin", "AdminPassword123")
        product = market.create_product(seller, {
            "title": "高等数学课本", "category": "教材", "price_cents": 2500,
            "item_condition": "八成新", "description": "书页完整",
            "trade_location": "教学楼大厅"})
        market.submit_product(seller, product["id"])
        product = market.review_product(admin, product["id"], "APPROVED")
        booked = market.reserve(buyer, product["id"], "demo-request-001",
                                product["version"], product["price_cents"])
        replay = market.reserve(buyer, product["id"], "demo-request-001",
                                product["version"], product["price_cents"])
        reservation_id = booked["reservation"]["id"]
        market.confirm_reservation(seller, reservation_id)
        completed = market.complete_reservation(seller, reservation_id)
        assert completed["reservation"]["status"] == "COMPLETED"
        assert completed["product"]["status"] == "SOLD"
        assert replay["idempotent_replay"]
        print("Demo OK: 草稿→审核→发布→预约→幂等重试→确认→成交")
        print("最终商品状态:", completed["product"]["status"])
        print("最终预约状态:", completed["reservation"]["status"])
        print("管理员可见日志条数:", len(market.admin_logs(admin)))


if __name__ == "__main__":
    main()
