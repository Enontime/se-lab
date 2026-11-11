"""REQ-001..020 extended acceptance tests on the unmodified v0.2 business code.

The test-only PBKDF2 work factor reduces setup time; production remains untouched.
The production password hash format and work factor are tested separately.
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import BusinessError
from app.db.connection import read_connection, write_transaction
from app.services import auth, audit
from app.services.market import Market


PRODUCT = dict(title="数据结构教材", category="教材", price_cents=1500,
               item_condition="九成新", description="无缺页", trade_location="图书馆")


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Speed up account creation only within this acceptance-test fixture.
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 3000)
    m = Market(tmp_path / "acceptance.sqlite3")
    m.initialize()
    seller = m.create_account("SellerA", "password123")
    buyer = m.create_account("BuyerA", "password123")
    other = m.create_account("BuyerB", "password123")
    admin = m.bootstrap_admin("AdminA", "password123")
    return m, seller, buyer, other, admin


def create(env, *, seller=None, **changes):
    m, own, buyer, other, admin = env
    return m.create_product(seller or own, {**PRODUCT, **changes})


def publish(env, *, seller=None, **changes):
    m, own, buyer, other, admin = env
    draft = create(env, seller=seller, **changes)
    m.submit_product(seller or own, draft["id"])
    return m.review_product(admin, draft["id"], "APPROVED")


def reserve(env, p, *, buyer=None, request="req-a", version=None, price=None):
    m, seller, default_buyer, other, admin = env
    return m.reserve(buyer or default_buyer, p["id"], request,
                     p["version"] if version is None else version,
                     p["price_cents"] if price is None else price)


def rejected(fn, code, *, status=None):
    with pytest.raises(BusinessError) as ex:
        fn()
    assert ex.value.code == code
    if status is not None:
        assert ex.value.status == status


def sql_value(m, sql, args=()):
    with read_connection(m.db_path) as conn:
        return conn.execute(sql, args).fetchone()[0]


# A. Accounts and roles (REQ-001/002/019)
@pytest.mark.parametrize("username", ["ab", "with space", "中文名", "a" * 33, "a@b"])
def test_a01_bad_username(env, username):
    # A 33-character username is rejected by generic length validation first.
    expected = "INVALID_INPUT" if len(username) > 32 else "INVALID_USERNAME"
    rejected(lambda: env[0].create_account(username, "goodpass123"), expected)


@pytest.mark.parametrize("password", ["short", "x" * 129, ""])
def test_a02_bad_password(env, password):
    rejected(lambda: env[0].create_account("ValidUser", password), "INVALID_PASSWORD")


def test_a03_casefold_username_unique(env):
    rejected(lambda: env[0].create_account("sellera", "password123"), "USERNAME_EXISTS")


def test_a04_login_case_insensitive_and_password_verification(env):
    m = env[0]
    assert m.login("SELLERA", "password123")["user"]["id"] == env[1]["id"]
    rejected(lambda: m.login("SELLERA", "incorrect"), "BAD_CREDENTIALS", status=401)


def test_a05_token_logout_and_no_plaintext_storage(env):
    m = env[0]
    t = m.login("BuyerA", "password123")["token"]
    assert m.identity(t)["id"] == env[2]["id"]
    assert "password123" not in sql_value(m, "SELECT password_hash FROM users WHERE username='BuyerA'")
    assert sql_value(m, "SELECT token_hash FROM auth_sessions WHERE user_id=?", (env[2]["id"],)) != t
    m.logout(t)
    rejected(lambda: m.identity(t), "UNAUTHORIZED", status=401)


def test_a06_expired_session_rejected(env):
    m = env[0]
    token = m.login("BuyerA", "password123")["token"]
    with write_transaction(m.db_path) as conn:
        conn.execute("UPDATE auth_sessions SET expires_at='2000-01-01T00:00:00+00:00'")
    rejected(lambda: m.identity(token), "UNAUTHORIZED")


def test_a07_admin_role_is_separate(env):
    m, seller, buyer, other, admin = env
    assert admin["account_type"] == "ADMIN"
    assert seller["account_type"] == "NORMAL"
    rejected(lambda: m.create_product(admin, PRODUCT), "FORBIDDEN", status=403)
    rejected(lambda: m.admin_stats(buyer), "FORBIDDEN", status=403)
    rejected(lambda: m.seller_stats(admin), "FORBIDDEN", status=403)


def test_a08_production_pbkdf2_contract():
    # Do not change the production constant in this independent test.
    assert auth.HASH_ITERATIONS >= 600_000
    stored = auth.hash_password("securePassword123")
    assert stored.startswith("pbkdf2_sha256$600000$")
    assert auth.verify_password("securePassword123", stored)
    assert not auth.verify_password("wrongPassword", stored)


# B. Product lifecycle and review (REQ-003..006/015)
@pytest.mark.parametrize("field,value", [
    ("price_cents", -1), ("price_cents", 3.5), ("price_cents", True),
    ("title", " "), ("title", 123), ("unknown", "x")
])
def test_b01_invalid_product_field(env, field, value):
    rejected(lambda: create(env, **{field: value}), "INVALID_INPUT")


def test_b02_partial_draft_and_submit_required_fields(env):
    m, seller, *_ = env
    p = m.create_product(seller, {"title": "草稿"})
    assert p["status"] == "DRAFT"
    rejected(lambda: m.submit_product(seller, p["id"]), "INCOMPLETE_PRODUCT")
    assert m.my_products(seller)[0]["status"] == "DRAFT"


def test_b03_non_owner_cannot_edit_submit_delist(env):
    m, seller, buyer, *_ = env
    p = create(env)
    rejected(lambda: m.edit_product(buyer, p["id"], {"title": "改名"}), "FORBIDDEN")
    rejected(lambda: m.submit_product(buyer, p["id"]), "FORBIDDEN")
    assert m.my_products(buyer) == []
    p2 = publish(env)
    rejected(lambda: m.delist_product(buyer, p2["id"]), "FORBIDDEN")


def test_b04_draft_reject_edit_resubmit_approve(env):
    m, seller, buyer, other, admin = env
    p = create(env)
    m.submit_product(seller, p["id"])
    rejected(lambda: m.review_product(buyer, p["id"], "APPROVED"), "FORBIDDEN")
    rejected(lambda: m.review_product(admin, p["id"], "REJECTED", " "), "INVALID_INPUT")
    p = m.review_product(admin, p["id"], "REJECTED", "描述需改进")
    assert p["status"] == "REJECTED"
    assert m.list_public() == []
    p = m.edit_product(seller, p["id"], {"description": "补全详情"})
    assert p["status"] == "REJECTED"
    m.submit_product(seller, p["id"])
    p = m.review_product(admin, p["id"], "APPROVED")
    assert p["status"] == "ON_SALE" and p["published_at"]
    assert sql_value(m, "SELECT COUNT(*) FROM product_reviews WHERE product_id=?", (p["id"],)) == 2


def test_b05_only_pending_can_be_reviewed(env):
    m, seller, buyer, other, admin = env
    p = create(env)
    rejected(lambda: m.review_product(admin, p["id"], "APPROVED"), "INVALID_STATUS")
    m.submit_product(seller, p["id"])
    rejected(lambda: m.review_product(admin, p["id"], "MAYBE"), "INVALID_DECISION")
    m.review_product(admin, p["id"], "APPROVED")
    rejected(lambda: m.review_product(admin, p["id"], "APPROVED"), "INVALID_STATUS")


def test_b06_delisted_can_resubmit_but_sold_cannot(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    m.delist_product(seller, p["id"])
    assert m.submit_product(seller, p["id"])["status"] == "PENDING_REVIEW"
    p = m.review_product(admin, p["id"], "APPROVED")
    b = reserve(env, p)["reservation"]
    m.confirm_reservation(seller, b["id"])
    m.complete_reservation(seller, b["id"])
    rejected(lambda: m.submit_product(seller, p["id"]), "INVALID_STATUS")
    rejected(lambda: m.delist_product(seller, p["id"]), "INVALID_STATUS")


def test_b07_violation_delist_reason_and_no_republish(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    rejected(lambda: m.delist_product(buyer, p["id"], violation=True, reason="违规"), "FORBIDDEN")
    rejected(lambda: m.delist_product(admin, p["id"], violation=True, reason=""), "INVALID_INPUT")
    p2 = m.delist_product(admin, p["id"], violation=True, reason="违规内容")["product"]
    assert p2["status"] == "VIOLATION_DELISTED" and p2["violation_reason"] == "违规内容"
    rejected(lambda: m.submit_product(seller, p["id"]), "INVALID_STATUS")


def test_b08_on_sale_key_field_requires_review(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    edited = m.edit_product(seller, p["id"], {"price_cents": 1700})
    assert edited["status"] == "DRAFT"
    assert edited["version"] > p["version"]
    assert m.list_public() == []
    rejected(lambda: reserve(env, p), "NOT_ON_SALE")
    rejected(lambda: m.review_product(admin, p["id"], "APPROVED"), "INVALID_STATUS")
    assert m.submit_product(seller, p["id"])["status"] == "PENDING_REVIEW"
    assert m.review_product(admin, p["id"], "APPROVED")["status"] == "ON_SALE"


def test_b09_on_sale_description_now_confirmed(env):
    m, seller, *_ = env
    p = publish(env)
    updated = m.edit_product(seller, p["id"], {"description": "new"})
    assert updated["status"] == "DRAFT" and updated["description"] == "new"


def test_b10_effective_booking_blocks_key_field_edit(env):
    m, seller, *_ = env
    p = publish(env)
    reserve(env, p)
    rejected(lambda: m.edit_product(seller, p["id"], {"title": "更新"}), "HAS_BOOKING")


# C. Reservation and transaction status machine (REQ-008..015)

def test_c01_create_and_derived_booked_view(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    assert b["status"] == "PENDING" and b["buyer_id"] == buyer["id"]
    assert m.public_detail(p["id"])["booked"] is True
    assert m.list_public(status="RESERVED")[0]["id"] == p["id"]


def test_c02_own_product_and_other_buyer_conflict(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    rejected(lambda: reserve(env, p, buyer=seller), "OWN_PRODUCT")
    reserve(env, p)
    rejected(lambda: reserve(env, p, buyer=other, request="req-b"), "PRODUCT_RESERVED")
    assert sql_value(m, "SELECT COUNT(*) FROM reservations WHERE product_id=?", (p["id"],)) == 1


def test_c03_same_request_replays_and_changed_fingerprint_conflicts(env):
    m = env[0]
    p = publish(env)
    x = reserve(env, p, request="duplicate")
    y = reserve(env, p, request="duplicate")
    assert x["reservation"]["id"] == y["reservation"]["id"]
    assert y["idempotent_replay"]
    rejected(lambda: reserve(env, p, request="duplicate", price=100), "REQUEST_REUSED")
    assert sql_value(m, "SELECT COUNT(*) FROM reservations WHERE product_id=?", (p["id"],)) == 1


def test_c04_cancel_old_request_then_new_request(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p, request="old")["reservation"]
    m.cancel_reservation(buyer, b["id"])
    old = reserve(env, p, request="old")
    assert old["idempotent_replay"] and old["reservation"]["status"] == "CANCELLED"
    newer = reserve(env, p, request="new")["reservation"]
    assert newer["id"] != b["id"] and newer["status"] == "PENDING"


def test_c05_stale_version_or_price(env):
    p = publish(env)
    rejected(lambda: reserve(env, p, version=p["version"] + 1), "STALE_PRODUCT")
    rejected(lambda: reserve(env, p, price=p["price_cents"] + 1), "STALE_PRODUCT")
    assert sql_value(env[0], "SELECT COUNT(*) FROM reservations") == 0


@pytest.mark.parametrize("status", ["DRAFT", "PENDING_REVIEW", "REJECTED", "DELISTED", "VIOLATION_DELISTED", "SOLD"])
def test_c06_cannot_reserve_non_sale(env, status):
    # Construct a state by DB injection only to isolate the reservation precondition;
    # the public API must never offer arbitrary status writes.
    m, seller, buyer, other, admin = env
    p = publish(env)
    with write_transaction(m.db_path) as conn:
        conn.execute("UPDATE products SET status=? WHERE id=?", (status, p["id"]))
    rejected(lambda: reserve(env, p), "NOT_ON_SALE")


def test_c07_seller_confirm_then_complete_history(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    assert m.confirm_reservation(seller, b["id"])["status"] == "CONFIRMED"
    result = m.complete_reservation(seller, b["id"])
    assert result["reservation"]["status"] == "COMPLETED"
    assert result["product"]["status"] == "SOLD"
    assert m.my_reservations(buyer)[0]["status"] == "COMPLETED"
    assert m.received_reservations(seller)[0]["status"] == "COMPLETED"
    rejected(lambda: m.cancel_reservation(buyer, b["id"]), "INVALID_STATUS")
    rejected(lambda: m.complete_reservation(seller, b["id"]), "INVALID_STATUS")


def test_c08_seller_rejection_reason_releases_booking(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    c = m.reject_reservation(seller, b["id"], "价格不合适")
    assert c["status"] == "CANCELLED" and c["cancel_source"] == "SELLER_REJECTED"
    assert c["cancel_reason"] == "价格不合适"
    assert m.public_detail(p["id"])["booked"] is False
    assert reserve(env, p, buyer=other, request="req-b")["reservation"]["status"] == "PENDING"


@pytest.mark.parametrize("confirm_first", [False, True])
def test_c09_buyer_cancels_pending_or_confirmed(env, confirm_first):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    if confirm_first:
        m.confirm_reservation(seller, b["id"])
    c = m.cancel_reservation(buyer, b["id"])
    assert c["status"] == "CANCELLED" and c["cancel_source"] == "BUYER"
    rejected(lambda: m.cancel_reservation(buyer, b["id"]), "INVALID_STATUS")
    assert m.public_detail(p["id"])["booked"] is False


def test_c10_unauthorized_booking_actions_rejected(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    rejected(lambda: m.confirm_reservation(other, b["id"]), "FORBIDDEN")
    rejected(lambda: m.reject_reservation(other, b["id"], "拒绝"), "FORBIDDEN")
    rejected(lambda: m.cancel_reservation(other, b["id"]), "FORBIDDEN")
    rejected(lambda: m.complete_reservation(other, b["id"]), "FORBIDDEN")
    rejected(lambda: m.confirm_reservation(admin, b["id"]), "FORBIDDEN")
    assert m.my_reservations(other) == []
    assert m.received_reservations(other) == []


def test_c11_confirm_only_pending_reject_reason_required(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    rejected(lambda: m.reject_reservation(seller, b["id"], " "), "INVALID_INPUT")
    rejected(lambda: m.complete_reservation(seller, b["id"]), "INVALID_STATUS")
    m.confirm_reservation(seller, b["id"])
    rejected(lambda: m.confirm_reservation(seller, b["id"]), "INVALID_STATUS")
    rejected(lambda: m.reject_reservation(seller, b["id"], "拒绝"), "INVALID_STATUS")


def test_c12_delist_cancels_pending_and_preserves_reason(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    out = m.delist_product(seller, p["id"])
    assert out["cancelled_reservations"] == 1
    assert out["product"]["status"] == "DELISTED"
    c = m.my_reservations(buyer)[0]
    assert c["id"] == b["id"] and c["cancel_source"] == "PRODUCT_DELISTED"
    assert c["cancel_reason"]
    rejected(lambda: reserve(env, p, buyer=other, request="after"), "NOT_ON_SALE")


def test_c13_violation_delist_cancels_confirmed_booking(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    m.confirm_reservation(seller, b["id"])
    out = m.delist_product(admin, p["id"], violation=True, reason="违规交易")
    assert out["cancelled_reservations"] == 1
    c = m.my_reservations(buyer)[0]
    assert c["status"] == "CANCELLED" and c["cancel_source"] == "VIOLATION_DELISTED"
    assert c["cancel_reason"] == "违规交易"
    rejected(lambda: m.confirm_reservation(seller, b["id"]), "INVALID_STATUS")


# D. DB constraints and transactional atomicity (REQ-012/015/017/020)

def test_d01_active_partial_unique_index(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    reserve(env, p)
    with pytest.raises(sqlite3.IntegrityError):
        with write_transaction(m.db_path) as conn:
            conn.execute("""INSERT INTO reservations
              (product_id,buyer_id,request_id,request_fingerprint,status,created_at)
              VALUES(?,?,?,?, 'CONFIRMED',?)""",
              (p["id"], other["id"], "direct", "fingerprint", datetime.now(timezone.utc).isoformat()))
    assert sql_value(m, "SELECT COUNT(*) FROM reservations") == 1


def test_d02_foreign_keys_and_status_checks(env):
    m, seller, buyer, other, admin = env
    with pytest.raises(sqlite3.IntegrityError):
        with write_transaction(m.db_path) as conn:
            conn.execute("""INSERT INTO products
                (seller_id,status,created_at,updated_at) VALUES(999999,'DRAFT','t','t')""")
    p = create(env)
    with pytest.raises(sqlite3.IntegrityError):
        with write_transaction(m.db_path) as conn:
            conn.execute("UPDATE products SET status='INVALID' WHERE id=?", (p["id"],))
    assert sql_value(m, "SELECT status FROM products WHERE id=?", (p["id"],)) == "DRAFT"


def test_d03_idempotency_unique_index(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    reserve(env, p, request="once")
    with pytest.raises(sqlite3.IntegrityError):
        with write_transaction(m.db_path) as conn:
            conn.execute("""INSERT INTO reservations
             (product_id,buyer_id,request_id,request_fingerprint,status,created_at)
             VALUES(?,?,?,?, 'CANCELLED',?)""",
             (p["id"], buyer["id"], "once", "different", datetime.now(timezone.utc).isoformat()))


def test_d04_atomic_rollback_when_log_write_fails(env, monkeypatch):
    m, seller, buyer, other, admin = env
    p = publish(env)
    before_logs = sql_value(m, "SELECT COUNT(*) FROM operation_logs")
    def broken(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    # Reservations module imports the helper directly; patch that reference.
    from app.services import reservations
    monkeypatch.setattr(reservations, "log_success", broken)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        reserve(env, p)
    assert sql_value(m, "SELECT COUNT(*) FROM reservations") == 0
    assert sql_value(m, "SELECT COUNT(*) FROM operation_logs") == before_logs


def test_d05_review_and_state_atomicity_on_log_failure(env, monkeypatch):
    m, seller, buyer, other, admin = env
    p = create(env)
    m.submit_product(seller, p["id"])
    from app.services import reviews
    def broken(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(reviews, "log_success", broken)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        m.review_product(admin, p["id"], "APPROVED")
    assert sql_value(m, "SELECT status FROM products WHERE id=?", (p["id"],)) == "PENDING_REVIEW"
    assert sql_value(m, "SELECT COUNT(*) FROM product_reviews") == 0


def test_d06_cross_product_not_shared_request_id(env):
    # REQ-013 scope is per buyer request_id, not per product.
    m, seller, buyer, other, admin = env
    p = publish(env)
    p2 = publish(env)
    reserve(env, p, request="global-key")
    rejected(lambda: reserve(env, p2, request="global-key"), "REQUEST_REUSED")


def test_d07_parallel_same_request_single_row(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    gate = Barrier(2)
    def attempt():
        gate.wait()
        return reserve(env, p, request="parallel-repeat")
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(attempt)
        b = pool.submit(attempt)
        x, y = a.result(), b.result()
    assert x["reservation"]["id"] == y["reservation"]["id"]
    assert {x["idempotent_replay"], y["idempotent_replay"]} == {False, True}
    assert sql_value(m, "SELECT COUNT(*) FROM reservations") == 1


def test_d08_parallel_delist_and_reserve_invariant(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    gate = Barrier(2)
    def book():
        gate.wait()
        try:
            return reserve(env, p, request="racing")
        except BusinessError:
            return None
    def delist():
        gate.wait()
        return m.delist_product(seller, p["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(book)
        b = pool.submit(delist)
        _ = a.result(), b.result()
    assert sql_value(m, "SELECT status FROM products WHERE id=?", (p["id"],)) == "DELISTED"
    assert sql_value(m, "SELECT COUNT(*) FROM reservations WHERE status IN ('PENDING','CONFIRMED')") == 0


# E. Public catalog, stats, logs (REQ-007/016/017/018)

def test_e01_keyword_category_price_combo(env):
    m = env[0]
    p1 = publish(env)
    p2 = publish(env, title="机械键盘", category="数码", price_cents=9000)
    assert [p["id"] for p in m.list_public(keyword="数据结构", category="教材", min_price=1500, max_price=1500)] == [p1["id"]]
    assert m.list_public(keyword="不存在") == []
    assert [p["id"] for p in m.list_public(category="数码")] == [p2["id"]]


def test_e02_private_products_excluded(env):
    m, seller, buyer, other, admin = env
    p = create(env)
    assert m.list_public() == []
    rejected(lambda: m.public_detail(p["id"]), "NOT_FOUND", status=404)
    m.submit_product(seller, p["id"])
    assert m.list_public() == []
    rejected(lambda: m.public_detail(p["id"]), "NOT_FOUND")
    m.review_product(admin, p["id"], "APPROVED")
    assert len(m.list_public()) == 1


def test_e03_pagination_and_bad_params(env):
    m = env[0]
    ps = [publish(env, title=f"item{i}") for i in range(5)]
    ids = [x["id"] for x in m.list_public(limit=2, offset=0)] + [x["id"] for x in m.list_public(limit=2, offset=2)]
    assert len(set(ids)) == 4
    assert m.list_public(offset=100) == []
    for opts in ({"min_price": 2000, "max_price": 100}, {"limit": 101}, {"offset": -1}, {"status": "DRAFT"}, {"availability": "WRONG"}):
        with pytest.raises(BusinessError):
            m.list_public(**opts)


def test_e04_derived_availability_after_cancel(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    assert m.list_public(status="AVAILABLE")[0]["booked"] == 0
    b = reserve(env, p)["reservation"]
    assert m.list_public(status="RESERVED")[0]["booked"] == 1
    m.cancel_reservation(buyer, b["id"])
    assert m.list_public(status="AVAILABLE")[0]["booked"] == 0


def test_e05_seller_statistics_isolation(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    seller2 = buyer
    m.create_product(seller2, {"title": "other"})
    own = m.seller_stats(seller)
    theirs = m.seller_stats(seller2)
    assert own["total"] == 1 and own["by_status"]["ON_SALE"] == 1
    assert theirs["total"] == 1 and theirs["by_status"]["DRAFT"] == 1
    assert m.seller_stats(other)["total"] == 0


def test_e06_admin_statistics_dates_and_completion(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    m.confirm_reservation(seller, b["id"])
    m.complete_reservation(seller, b["id"])
    today = datetime.now(timezone.utc).date().isoformat()
    d = m.admin_stats(admin, start=today, end=today)
    assert sum(x["n"] for x in d["published"]) == 1
    assert sum(x["n"] for x in d["completed"]) == 1
    assert m.admin_stats(admin, start="2000-01-01", end="2000-12-31") == {"published": [], "completed": []}
    rejected(lambda: m.admin_stats(buyer), "FORBIDDEN")


def test_e07_audit_required_actions_and_fields(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    m.confirm_reservation(seller, b["id"])
    m.complete_reservation(seller, b["id"])
    logs = m.admin_logs(admin)
    required = {"PRODUCT_DRAFT_CREATE", "PRODUCT_SUBMIT", "PRODUCT_REVIEW_APPROVED", "RESERVATION_CREATE", "RESERVATION_CONFIRM", "RESERVATION_COMPLETE"}
    assert required.issubset({x["action"] for x in logs})
    for log in logs:
        assert log["actor_id"] and log["created_at"] and log["target_id"] and log["result"] == "SUCCESS"


def test_e08_audit_filter_pagination_and_role(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    reserve(env, p)
    all_logs = m.admin_logs(admin)
    matches = m.admin_logs(admin, action="RESERVATION_CREATE", actor_id=buyer["id"])
    assert len(matches) == 1
    assert m.admin_logs(admin, limit=1, offset=1)[0]["id"] == all_logs[1]["id"]
    rejected(lambda: m.admin_logs(buyer), "FORBIDDEN")
    rejected(lambda: m.admin_logs(admin, start="2026-99-99"), "INVALID_DATE")
    rejected(lambda: m.admin_logs(admin, start="2026-11-10", end="2026-01-01"), "INVALID_RANGE")


def test_e09_nonexistent_ids_return_not_found(env):
    m, seller, buyer, other, admin = env
    rejected(lambda: m.public_detail(999999), "NOT_FOUND", status=404)
    rejected(lambda: m.confirm_reservation(seller, 999999), "NOT_FOUND", status=404)
    rejected(lambda: m.edit_product(seller, 999999, {"title":"x"}), "NOT_FOUND", status=404)


# F. Unconfirmed specification and negative contract checks

def test_f01_no_automatic_booking_expiration(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    b = reserve(env, p)["reservation"]
    assert "expires_at" not in b
    assert m.my_reservations(buyer)[0]["status"] == "PENDING"


def test_f02_no_online_payment_shipping_chat_tables(env):
    m = env[0]
    with read_connection(m.db_path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not names.intersection({"payments", "shipping", "messages", "recommendations", "ratings"})


def test_f03_admin_cannot_reserve(env):
    m, seller, buyer, other, admin = env
    p = publish(env)
    rejected(lambda: reserve(env, p, buyer=admin), "FORBIDDEN")
