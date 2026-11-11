"""HTTP contract for private moderation history and human-friendly actor search."""
import pytest

pytest.importorskip('flask')
from app import create_app


@pytest.fixture
def client_market(tmp_path):
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'v043.sqlite3')})
    client = app.test_client()
    market = app.extensions['campus_market_service']
    seller = market.create_account('seller_q43', 'SellerPassword123')
    admin = market.bootstrap_admin('admin_q43', 'AdminPassword123')
    return client, market, seller, admin


def _bearer(market, username, password):
    return {'Authorization': f'Bearer {market.login(username, password)["token"]}'}


def test_reviewed_http_admin_only_and_no_pending_leak(client_market):
    client, market, seller, admin = client_market
    pid = market.create_product(seller, {'title': '教材', 'category': '书籍',
        'price_cents': 2300, 'item_condition': '九成新',
        'description': '期末笔记', 'trade_location': '图书馆'})['id']
    market.submit_product(seller, pid)
    h_admin = _bearer(market, 'admin_q43', 'AdminPassword123')
    h_seller = _bearer(market, 'seller_q43', 'SellerPassword123')
    route = '/api/admin/products/reviewed?decision=APPROVED&limit=21&offset=0'
    assert client.get(route).status_code == 401
    assert client.get(route, headers=h_seller).status_code == 403
    assert client.get(route, headers=h_admin).json['items'] == []
    market.review_product(admin, pid, 'APPROVED')
    items = client.get(route, headers=h_admin).json['items']
    assert [item['id'] for item in items] == [pid]
    assert client.get(route.replace('APPROVED', 'INVALID'), headers=h_admin).status_code == 400
    assert client.get(route.replace('offset=0', 'offset=1'), headers=h_admin).json['items'] == []


def test_admin_logs_actor_query_by_username(client_market):
    client, market, seller, admin = client_market
    market.create_product(seller, {'title': '可查日志'})
    h_admin = _bearer(market, 'admin_q43', 'AdminPassword123')
    resp = client.get('/api/admin/logs?actor_query=seller_q43&limit=100', headers=h_admin)
    assert resp.status_code == 200
    assert len(resp.json['items']) >= 1
    assert all(x['actor_name'] == 'seller_q43' for x in resp.json['items'])
    assert client.get('/api/admin/logs?actor_query=nonexistent', headers=h_admin).json['items'] == []
