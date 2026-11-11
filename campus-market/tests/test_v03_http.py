"""v0.3 API integration; run with Flask installed on Windows or CI."""
from __future__ import annotations

import pytest

pytest.importorskip("flask")
from app import create_app


@pytest.fixture
def http_v03(tmp_path, monkeypatch):
    from app.services import auth
    # A test-only speedup; production HASH_ITERATIONS remains unchanged.
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 3000)
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "api.sqlite3")})
    m = app.extensions["campus_market_service"]
    m.bootstrap_admin("admin03", "password123")
    client = app.test_client()

    def login(name, *, register=True):
        payload = {"username": name, "password": "password123"}
        if register:
            assert client.post("/api/auth/register", json=payload).status_code == 201
        token = client.post("/api/auth/login", json=payload).json["token"]
        return {"Authorization": f"Bearer {token}"}

    tokens = {key: login(key, register=(key != "admin03"))
              for key in ("seller03", "buyer03", "other03", "admin03")}
    return client, m, tokens


def new_product(client, headers):
    payload = {"title": "程序设计书", "category": "教材", "price_cents": 2500,
               "item_condition": "新", "description": "课本", "trade_location": "图书馆"}
    resp = client.post("/api/products", json=payload, headers=headers)
    assert resp.status_code == 201, resp.json
    return resp.json


def approved(http_v03):
    client, m, auth = http_v03
    p = new_product(client, auth["seller03"])
    assert client.post(f"/api/products/{p['id']}/submit", headers=auth["seller03"]).status_code == 200
    res = client.post(f"/api/admin/products/{p['id']}/review", headers=auth["admin03"],
                      json={"decision": "APPROVED"})
    assert res.status_code == 200, res.json
    return res.json


def check_error(resp, status, code):
    assert resp.status_code == status, resp.json
    assert resp.json["error"]["code"] == code, resp.json


def test_api03_admin_violation_resolution_full_flow(http_v03):
    client, m, auth = http_v03
    p = approved(http_v03)
    pid = p["id"]
    assert client.post(f"/api/admin/products/{pid}/violation-delist",
                       headers=auth["admin03"], json={"reason": "违规"}).status_code == 200
    check_error(client.post(f"/api/admin/products/{pid}/violation-resolve",
                            headers=auth["buyer03"], json={"handling_note": "check"}),
                403, "FORBIDDEN")
    check_error(client.post(f"/api/products/{pid}/submit", headers=auth["seller03"]),
                409, "INVALID_STATUS")
    assert len(client.get("/api/admin/violations", headers=auth["admin03"]).json["items"]) == 1
    assert len(client.get(f"/api/my/products/{pid}/violations",
                          headers=auth["seller03"]).json["items"]) == 1
    result = client.post(f"/api/admin/products/{pid}/violation-resolve",
                         headers=auth["admin03"], json={"handling_note": "管理员已核查"})
    assert result.status_code == 200 and result.json["product"]["status"] == "VIOLATION_DELISTED"
    check_error(client.get(f"/api/products/{pid}"), 404, "NOT_FOUND")
    assert client.post(f"/api/products/{pid}/submit", headers=auth["seller03"]).json["status"] == "PENDING_REVIEW"
    assert client.post(f"/api/admin/products/{pid}/review", headers=auth["admin03"],
                       json={"decision": "APPROVED"}).json["status"] == "ON_SALE"


def test_api03_private_product_detail_review_history(http_v03):
    client, m, auth = http_v03
    p = approved(http_v03)
    pid = p["id"]
    assert client.get(f"/api/my/products/{pid}", headers=auth["seller03"]).json["id"] == pid
    check_error(client.get(f"/api/my/products/{pid}", headers=auth["buyer03"]), 404, "NOT_FOUND")
    check_error(client.get(f"/api/my/products/{pid}"), 401, "UNAUTHORIZED")
    assert client.get(f"/api/my/products/{pid}/reviews", headers=auth["seller03"]).json["items"][0]["decision"] == "APPROVED"
    check_error(client.get(f"/api/my/products/{pid}/reviews", headers=auth["buyer03"]), 404, "NOT_FOUND")
    assert len(client.get(f"/api/admin/products/{pid}/reviews", headers=auth["admin03"]).json["items"]) == 1
    check_error(client.get(f"/api/admin/products/{pid}/reviews", headers=auth["seller03"]), 403, "FORBIDDEN")


def test_api03_private_reservation_detail_and_dashboard(http_v03):
    client, m, auth = http_v03
    p = approved(http_v03)
    booking = client.post("/api/reservations", headers=auth["buyer03"], json={
        "product_id": p["id"], "request_id": "v03-http", "expected_version": p["version"],
        "expected_price_cents": p["price_cents"]})
    assert booking.status_code == 201, booking.json
    b = booking.json["reservation"]
    assert b["expected_price_cents"] == 2500
    path = f"/api/reservations/{b['id']}"
    assert client.get(path, headers=auth["buyer03"]).json["counterparty_name"] == "seller03"
    assert client.get(path, headers=auth["seller03"]).json["id"] == b["id"]
    check_error(client.get(path, headers=auth["other03"]), 404, "NOT_FOUND")
    assert client.get("/api/my/dashboard", headers=auth["buyer03"]).json["reservations"]["as_buyer"]["total"] == 1
    assert len(client.get("/api/reservations/mine?status=PENDING", headers=auth["buyer03"]).json["items"]) == 1
    check_error(client.get("/api/reservations/mine?status=INVALID", headers=auth["buyer03"]),
                400, "INVALID_STATUS_FILTER")


def test_api03_public_facets_reservation_counts(http_v03):
    client, m, auth = http_v03
    p = approved(http_v03)
    assert client.get("/api/products/facets").json["available"] == 1
    assert client.get("/api/products/count").json["count"] == 1
    client.post("/api/reservations", headers=auth["buyer03"], json={
        "product_id": p["id"], "request_id": "counts", "expected_version": p["version"],
        "expected_price_cents": p["price_cents"]})
    assert client.get("/api/products/facets").json["reserved"] == 1
    assert client.get("/api/products/count?availability=AVAILABLE").json["count"] == 0
    check_error(client.get("/api/products/count?availability=DRAFT"), 400, "INVALID_INPUT")


def test_api03_moderation_queue_filters_and_permissions(http_v03):
    client, m, auth = http_v03
    p = new_product(client, auth["seller03"])
    client.post(f"/api/products/{p['id']}/submit", headers=auth["seller03"])
    queue = client.get("/api/admin/products/pending?keyword=%E7%A8%8B%E5%BA%8F",
                       headers=auth["admin03"])
    assert queue.status_code == 200 and queue.json["items"][0]["id"] == p["id"]
    assert client.get("/api/admin/products/workload", headers=auth["admin03"]).json["PENDING_REVIEW"] == 1
    check_error(client.get("/api/admin/products/pending", headers=auth["buyer03"]), 403, "FORBIDDEN")
    check_error(client.get("/api/admin/products/pending?limit=101", headers=auth["admin03"]),
                400, "INVALID_INPUT")


def test_api03_audit_admin_and_invariants(http_v03):
    client, m, auth = http_v03
    p = approved(http_v03)
    pid = p["id"]
    integrity = client.get("/api/admin/integrity", headers=auth["admin03"])
    assert integrity.status_code == 200 and integrity.json["ok"] is True
    assert client.get(f"/api/admin/integrity/products/{pid}",
                      headers=auth["admin03"]).json["invariants_ok"] is True
    assert client.get(f"/api/admin/products/{pid}/timeline",
                      headers=auth["admin03"]).json["items"]
    assert client.get("/api/admin/activity/summary", headers=auth["admin03"]).json["total"] >= 2
    assert client.get("/api/admin/logs?target_type=Product&target_id=" + str(pid),
                      headers=auth["admin03"]).json["items"]
    check_error(client.get("/api/admin/integrity", headers=auth["buyer03"]), 403, "FORBIDDEN")
    check_error(client.get("/api/admin/activity/summary?start=invalid", headers=auth["admin03"]),
                400, "INVALID_DATE")


def test_api03_resolve_validation_and_no_implicit_publish(http_v03):
    client, m, auth = http_v03
    p = approved(http_v03)
    pid = p["id"]
    client.post(f"/api/admin/products/{pid}/violation-delist", headers=auth["admin03"],
                json={"reason": "违反商品发布规则"})
    check_error(client.post(f"/api/admin/products/{pid}/violation-resolve",
                            headers=auth["admin03"], json={"handling_note": ""}),
                400, "INVALID_INPUT")
    check_error(client.post(f"/api/admin/products/{pid}/violation-resolve",
                            headers=auth["admin03"], json={"note": "unknown"}),
                400, "UNKNOWN_FIELD")
    result = client.post(f"/api/admin/products/{pid}/violation-resolve",
                         headers=auth["admin03"], json={"handling_note": "已经核查"})
    assert result.status_code == 200
    check_error(client.get(f"/api/products/{pid}"), 404, "NOT_FOUND")
