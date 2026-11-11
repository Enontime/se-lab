"""v0.4.4 Flask HTTP contract; skipped explicitly when Flask is unavailable."""
import pytest

pytest.importorskip("flask")
from app import create_app
from app.services import auth


@pytest.fixture
def http_v044(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 3000)
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test_v044.sqlite3")})
    m = app.extensions["campus_market_service"]
    seller = m.create_account("v044seller", "goodPassword123")
    buyer = m.create_account("v044buyer", "goodPassword123")
    admin = m.bootstrap_admin("v044admin", "goodPassword123")
    tokens = {}
    for user in (seller, buyer, admin):
        token = m.login(user["username"], "goodPassword123")["token"]
        tokens[user["account_type"] + "_" + user["username"]] = {"Authorization": "Bearer " + token}
    return app.test_client(), m, seller, buyer, admin, tokens


def _setup(http_v044):
    client, m, seller, buyer, admin, tokens = http_v044
    product = m.create_product(seller, {
        "title": "二手教材", "category": "书籍", "price_cents": 2500,
        "item_condition": "八成新", "description": "无缺页", "trade_location": "校门口",
    })
    m.submit_product(seller, product["id"])
    product = m.review_product(admin, product["id"], "APPROVED")
    return product, tokens["NORMAL_v044seller"], tokens["NORMAL_v044buyer"], tokens["ADMIN_v044admin"]


def test_v044_http_public_seller_name_and_unpublished_visibility(http_v044):
    client, m, seller, buyer, admin, tokens = http_v044
    p, hs, hb, ha = _setup(http_v044)
    url = f"/api/products/{p['id']}"
    detail = client.get(url)
    assert detail.status_code == 200
    assert detail.json["seller_name"] == "v044seller"
    assert "password_hash" not in detail.json
    change = client.patch(url, headers=hs, json={"trade_location": "北门"})
    assert change.status_code == 200
    assert change.json["status"] == "DRAFT"
    assert client.get(url).status_code == 404
    assert client.get("/api/products").json["items"] == []
    assert client.get(f"/api/my/products/{p['id']}", headers=hs).json["trade_location"] == "北门"
    assert client.get(f"/api/my/products/{p['id']}", headers=hb).status_code == 404
    assert client.post(f"/api/admin/products/{p['id']}/review", headers=ha,
                       json={"decision": "APPROVED"}).status_code == 409
    assert client.post(f"/api/products/{p['id']}/submit", headers=hs).json["status"] == "PENDING_REVIEW"
    assert client.post(f"/api/admin/products/{p['id']}/review", headers=ha,
                       json={"decision": "APPROVED"}).json["status"] == "ON_SALE"
    assert client.get(url).json["seller_name"] == "v044seller"


@pytest.mark.parametrize("status", ["PENDING", "CONFIRMED"])
def test_v044_http_booking_blocks_edit(http_v044, status):
    client, m, seller, buyer, admin, tokens = http_v044
    p, hs, hb, ha = _setup(http_v044)
    b = m.reserve(buyer, p["id"], "first", p["version"], p["price_cents"])["reservation"]
    if status == "CONFIRMED":
        m.confirm_reservation(seller, b["id"])
    response = client.patch(f"/api/products/{p['id']}", headers=hs,
                            json={"category": "家电", "description": "重写"})
    assert response.status_code == 409
    assert response.json["error"]["code"] == "HAS_BOOKING"
    assert client.get(f"/api/products/{p['id']}").status_code == 200


def test_v044_http_all_six_fields_and_noop(http_v044):
    client, m, seller, buyer, admin, tokens = http_v044
    p, hs, hb, ha = _setup(http_v044)
    url = f"/api/products/{p['id']}"
    original = {k: p[k] for k in ("title", "category", "price_cents", "item_condition", "description", "trade_location")}
    no_op = client.patch(url, headers=hs, json=original)
    assert no_op.status_code == 400 and no_op.json["error"]["code"] == "NO_CHANGES"
    assert client.get(url).status_code == 200
    changed = {"title": "其他", "category": "数码", "price_cents": 0,
               "item_condition": "全新", "description": "完全不同", "trade_location": "食堂"}
    response = client.patch(url, headers=hs, json=changed)
    assert response.status_code == 200 and response.json["status"] == "DRAFT"
    for key, value in changed.items():
        assert response.json[key] == value


def test_v044_http_offsets_for_logs_and_booking(http_v044):
    client, m, seller, buyer, admin, tokens = http_v044
    p, hs, hb, ha = _setup(http_v044)
    for i in range(101):
        rid = m.reserve(buyer, p["id"], f"http-{i}", p["version"], p["price_cents"])["reservation"]["id"]
        m.cancel_reservation(buyer, rid)
    for endpoint, headers in [
        ("/api/reservations/mine", hb), ("/api/reservations/received", hs),
        ("/api/admin/logs", ha),
    ]:
        first = client.get(endpoint + "?limit=21&offset=0", headers=headers)
        later = client.get(endpoint + "?limit=21&offset=100", headers=headers)
        assert first.status_code == later.status_code == 200
        assert len(first.json["items"]) == 21
        assert later.json["items"]
        assert first.json["items"][0]["id"] != later.json["items"][0]["id"]
    assert client.get("/api/admin/logs?limit=21&offset=0", headers=hb).status_code == 403
