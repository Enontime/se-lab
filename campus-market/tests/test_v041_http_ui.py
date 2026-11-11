"""HTTP regression of the new protected admin detail endpoint."""
import pytest

pytest.importorskip("flask")
from app import create_app


@pytest.fixture
def detail_http(tmp_path, monkeypatch):
    from app.services import auth
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 3000)
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "v041.sqlite3")})
    market = app.extensions["campus_market_service"]
    market.bootstrap_admin("detailadmin", "Password123")
    client = app.test_client()

    def login(name, register=False):
        payload = {"username": name, "password": "Password123"}
        if register:
            assert client.post("/api/auth/register", json=payload).status_code == 201
        result = client.post("/api/auth/login", json=payload)
        assert result.status_code == 200
        return {"Authorization": "Bearer " + result.json["token"]}

    seller = login("detailseller", register=True)
    buyer = login("detailbuyer", register=True)
    admin = login("detailadmin")
    return client, seller, buyer, admin


def _pending(client, seller):
    response = client.post("/api/products", headers=seller, json={
        "title": "操作系统讲义", "category": "教材", "price_cents": 3300,
        "item_condition": "八成新", "description": "页面完整，无破损",
        "trade_location": "教学楼一层",
    })
    assert response.status_code == 201
    product = response.json
    assert client.post(f"/api/products/{product['id']}/submit", headers=seller).status_code == 200
    return product["id"]


def test_v041_admin_detail_visibility_and_http_status(detail_http):
    client, seller, buyer, admin = detail_http
    pid = _pending(client, seller)
    path = f"/api/admin/products/{pid}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=seller).status_code == 403
    assert client.get(path, headers=buyer).status_code == 403
    response = client.get(path, headers=admin)
    assert response.status_code == 200
    assert response.json["status"] == "PENDING_REVIEW"
    assert response.json["seller_name"] == "detailseller"
    assert response.json["description"] == "页面完整，无破损"
    assert response.json["trade_location"] == "教学楼一层"
    assert client.get(f"/api/products/{pid}").status_code == 404
    assert client.get("/api/admin/products/999999", headers=admin).status_code == 404


def test_v041_detail_works_after_moderation(detail_http):
    client, seller, buyer, admin = detail_http
    pid = _pending(client, seller)
    response = client.post(f"/api/admin/products/{pid}/review", headers=admin,
                           json={"decision": "APPROVED"})
    assert response.status_code == 200
    response = client.get(f"/api/admin/products/{pid}", headers=admin)
    assert response.status_code == 200
    assert response.json["status"] == "ON_SALE"
    assert client.get(f"/api/products/{pid}").status_code == 200
