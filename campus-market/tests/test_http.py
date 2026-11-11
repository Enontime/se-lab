import pytest

flask = pytest.importorskip("flask")

from app import create_app


def test_api_full_path(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "api.sqlite3")})
    client = app.test_client()
    assert client.get("/api/health").json["status"] == "ok"
    assert client.post("/api/auth/register", json={"username": "alice", "password": "Alicepwd123"}).status_code == 201
    assert client.post("/api/auth/register", json={"username": "bob", "password": "Bobspwd123"}).status_code == 201
    alice = client.post("/api/auth/login", json={"username": "alice", "password": "Alicepwd123"}).json["token"]
    bob = client.post("/api/auth/login", json={"username": "bob", "password": "Bobspwd123"}).json["token"]
    headers_a = {"Authorization": f"Bearer {alice}"}
    headers_b = {"Authorization": f"Bearer {bob}"}
    fields = {"title": "book", "category": "教材", "price_cents": 100,
              "item_condition": "九成新", "description": "练习册", "trade_location": "校门口"}
    product = client.post("/api/products", json=fields, headers=headers_a).json
    assert product["status"] == "DRAFT"
    assert client.post(f"/api/products/{product['id']}/submit", headers=headers_a).status_code == 200
    assert client.get("/api/products").json["items"] == []
    admin = app.extensions["campus_market_service"].bootstrap_admin("rootadmin", "AdminPassword123")
    token = client.post("/api/auth/login", json={"username": "rootadmin", "password": "AdminPassword123"}).json["token"]
    headers_admin = {"Authorization": f"Bearer {token}"}
    assert client.post(f"/api/admin/products/{product['id']}/review", json={"decision": "APPROVED"}, headers=headers_admin).status_code == 200
    public = client.get(f"/api/products/{product['id']}").json
    reserved = client.post("/api/reservations", json={"product_id": product["id"],
        "request_id": "http-req-1", "expected_version": public["version"],
        "expected_price_cents": public["price_cents"]}, headers=headers_b)
    assert reserved.status_code == 201
    assert client.get("/api/reservations/mine", headers=headers_b).json["items"][0]["status"] == "PENDING"
    assert client.get("/api/admin/logs", headers=headers_b).status_code == 403
