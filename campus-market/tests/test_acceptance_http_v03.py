"""HTTP behavior and authorization. Runs on the user's machine with Flask installed."""
from __future__ import annotations

import pytest
flask = pytest.importorskip("flask")
from app import create_app


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    from app.services import auth
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 3000)
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "httpqa.sqlite3")})
    m = app.extensions["campus_market_service"]
    m.bootstrap_admin("administrator", "password123")
    client = app.test_client()
    def register_login(name):
        res = client.post("/api/auth/register", json={"username": name, "password": "password123"})
        assert res.status_code == 201, res.json
        token = client.post("/api/auth/login", json={"username": name, "password": "password123"}).json["token"]
        return {"Authorization": f"Bearer {token}"}
    seller = register_login("sellerhttp")
    buyer = register_login("buyerhttp")
    other = register_login("otherhttp")
    admin_token = client.post("/api/auth/login", json={"username": "administrator", "password": "password123"}).json["token"]
    admin = {"Authorization": f"Bearer {admin_token}"}
    return client, m, seller, buyer, other, admin


def published(api_env):
    client, m, seller, buyer, other, admin = api_env
    p = client.post("/api/products", json={
        "title":"课本", "category":"教材", "price_cents": 1800,
        "item_condition":"九成新", "description":"无缺页", "trade_location":"门口"}, headers=seller)
    assert p.status_code == 201, p.json
    p = p.json
    assert client.post(f"/api/products/{p['id']}/submit", headers=seller).status_code == 200
    approved = client.post(f"/api/admin/products/{p['id']}/review",
                           json={"decision":"APPROVED"}, headers=admin)
    assert approved.status_code == 200, approved.json
    return approved.json


def code(resp, expected_status, expected_code):
    assert resp.status_code == expected_status, resp.json
    assert resp.json["error"]["code"] == expected_code, resp.json


def test_h01_health_is_public(api_env):
    client, *_ = api_env
    assert client.get("/api/health").json == {"status": "ok"}


@pytest.mark.parametrize("path", [
    "/api/auth/me", "/api/my/products", "/api/reservations/mine",
    "/api/reservations/received", "/api/stats/seller",
    "/api/admin/stats", "/api/admin/logs"])
def test_h02_protected_get_requires_auth(api_env, path):
    code(api_env[0].get(path), 401, "UNAUTHORIZED")


def test_h03_bad_and_revoked_tokens(api_env):
    client, m, seller, buyer, other, admin = api_env
    code(client.get("/api/auth/me", headers={"Authorization":"Bearer INVALID"}), 401, "UNAUTHORIZED")
    assert client.get("/api/auth/me", headers=seller).status_code == 200
    assert client.post("/api/auth/logout", headers=seller).json == {"ok": True}
    code(client.get("/api/auth/me", headers=seller), 401, "UNAUTHORIZED")


@pytest.mark.parametrize("payload", [None, [], "hello", {"username":"bad"}])
def test_h04_register_bad_body_or_missing_fields(api_env, payload):
    client = api_env[0]
    if payload is None:
        resp = client.post("/api/auth/register", data="broken", content_type="text/plain")
        code(resp, 400, "INVALID_JSON")
    elif isinstance(payload, dict):
        code(client.post("/api/auth/register", json=payload), 400, "INVALID_PASSWORD")
    else:
        code(client.post("/api/auth/register", json=payload), 400, "INVALID_JSON")


def test_h05_admin_routes_authorization(api_env):
    client, m, seller, buyer, other, admin = api_env
    p = published(api_env)
    code(client.post(f"/api/admin/products/{p['id']}/review", json={"decision":"APPROVED"}, headers=buyer), 403, "FORBIDDEN")
    code(client.post(f"/api/admin/products/{p['id']}/violation-delist", json={"reason":"违规"}, headers=buyer), 403, "FORBIDDEN")
    code(client.get("/api/admin/logs", headers=buyer), 403, "FORBIDDEN")
    code(client.get("/api/admin/stats", headers=buyer), 403, "FORBIDDEN")
    code(client.post("/api/products", json={"title":"x"}, headers=admin), 403, "FORBIDDEN")


def test_h06_private_product_visibility(api_env):
    client, m, seller, buyer, other, admin = api_env
    draft = client.post("/api/products", json={"title":"未审核"}, headers=seller).json
    assert client.get("/api/products").json["items"] == []
    code(client.get(f"/api/products/{draft['id']}"), 404, "NOT_FOUND")
    own = client.get("/api/my/products", headers=seller).json["items"]
    assert len(own) == 1 and own[0]["status"] == "DRAFT"
    assert client.get("/api/my/products", headers=buyer).json["items"] == []


def test_h07_public_filters_and_query_validation(api_env):
    client, m, seller, buyer, other, admin = api_env
    p = published(api_env)
    r = client.get("/api/products?keyword=%E8%AF%BE%E6%9C%AC&category=%E6%95%99%E6%9D%90&max_price=2000")
    assert r.status_code == 200 and r.json["items"][0]["id"] == p["id"]
    code(client.get("/api/products?min_price=abc"), 400, "INVALID_QUERY")
    code(client.get("/api/products?limit=101"), 400, "INVALID_INPUT")
    code(client.get("/api/products?min_price=200&max_price=100"), 400, "INVALID_RANGE")


def test_h08_reservation_http_errors_and_idempotency(api_env):
    client, m, seller, buyer, other, admin = api_env
    p = published(api_env)
    body = {"product_id":p["id"], "request_id":"same", "expected_version":p["version"],
            "expected_price_cents":p["price_cents"]}
    code(client.post("/api/reservations", json=body), 401, "UNAUTHORIZED")
    code(client.post("/api/reservations", json=body, headers=seller), 403, "OWN_PRODUCT")
    first = client.post("/api/reservations", json=body, headers=buyer)
    assert first.status_code == 201 and not first.json["idempotent_replay"]
    again = client.post("/api/reservations", json=body, headers=buyer)
    assert again.status_code == 201 and again.json["idempotent_replay"]
    assert first.json["reservation"]["id"] == again.json["reservation"]["id"]
    code(client.post("/api/reservations", json={**body,"request_id":"other"}, headers=other), 409, "PRODUCT_RESERVED")
    code(client.post("/api/reservations", json={**body,"request_id":"same", "expected_price_cents":1}, headers=buyer), 409, "REQUEST_REUSED")


def test_h09_seller_and_buyer_operation_permissions(api_env):
    client, m, seller, buyer, other, admin = api_env
    p = published(api_env)
    req = {"product_id":p["id"], "request_id":"go", "expected_version":p["version"],
           "expected_price_cents":p["price_cents"]}
    b = client.post("/api/reservations", json=req, headers=buyer).json["reservation"]
    code(client.post(f"/api/reservations/{b['id']}/confirm", headers=other), 403, "FORBIDDEN")
    code(client.post(f"/api/reservations/{b['id']}/cancel", headers=other), 403, "FORBIDDEN")
    code(client.post(f"/api/products/{p['id']}/delist", headers=other), 403, "FORBIDDEN")
    assert client.get("/api/reservations/received", headers=seller).json["items"][0]["id"] == b["id"]
    assert client.get("/api/reservations/received", headers=other).json["items"] == []
    assert client.post(f"/api/reservations/{b['id']}/confirm", headers=seller).json["status"] == "CONFIRMED"
    assert client.post(f"/api/reservations/{b['id']}/complete", headers=seller).json["product"]["status"] == "SOLD"
    code(client.post(f"/api/reservations/{b['id']}/cancel", headers=buyer), 409, "INVALID_STATUS")


def test_h10_admin_log_search_filter(api_env):
    client, m, seller, buyer, other, admin = api_env
    p = published(api_env)
    res = client.get("/api/admin/logs?action=PRODUCT_REVIEW_APPROVED&limit=1", headers=admin)
    assert res.status_code == 200
    assert len(res.json["items"]) == 1 and res.json["items"][0]["action"] == "PRODUCT_REVIEW_APPROVED"
    code(client.get("/api/admin/logs?start=2026-13-40", headers=admin), 400, "INVALID_DATE")
    code(client.get("/api/admin/logs?actor_id=nope", headers=admin), 400, "INVALID_QUERY")
