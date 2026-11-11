"""v0.4 Web UI smoke checks. Exercises served HTML/assets and API boundaries.

These tests run in the same Flask test environment as the existing HTTP suite.
The browser's layout/interaction smoke is additionally verified separately.
"""
from pathlib import Path

import pytest

pytest.importorskip("flask")
from app import create_app


@pytest.fixture
def client_v04(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "ui.sqlite3")})
    return app.test_client()


def test_ui04_home_is_served_not_api_404(client_v04):
    resp = client_v04.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert "校园拾集" in resp.get_data(as_text=True)
    assert "static/css/site.css" in resp.get_data(as_text=True)
    assert "static/js/site.js" in resp.get_data(as_text=True)


def test_ui04_static_assets_served(client_v04):
    css = client_v04.get("/static/css/site.css")
    js = client_v04.get("/static/js/site.js")
    assert css.status_code == 200 and ".catalog-layout" in css.get_data(as_text=True)
    assert js.status_code == 200 and "async function productDetail" in js.get_data(as_text=True)
    assert "sessionStorage" in js.get_data(as_text=True)


def test_ui04_routes_cover_all_original_screen_ids():
    js = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "site.js"
    source = js.read_text(encoding="utf-8")
    for route in ("/browse", "/product/", "/auth", "/publish", "/mine", "/bookings",
                  "/received", "/review", "/seller-stats", "/admin-stats", "/logs"):
        assert route in source
    # The UI only uses the server's established API, not a local mirror database.
    assert 'fetch(`/api${path}`' in source


def test_ui04_still_preserves_api_health_and_login_guard(client_v04):
    assert client_v04.get("/api/health").json == {"status": "ok"}
    resp = client_v04.get("/api/my/products")
    assert resp.status_code == 401
    assert resp.json["error"]["code"] == "UNAUTHORIZED"


def test_ui04_no_backend_test_code_mixed_into_static_assets():
    css = (Path(__file__).resolve().parents[1] / "app/static/css/site.css").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "app/static/js/site.js").read_text(encoding="utf-8")
    assert "DROP TABLE" not in js
    assert "DB_PASSWORD" not in js
    assert "flask --app" not in css
