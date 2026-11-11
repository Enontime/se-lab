"""Thin HTTP adapter; the Market coordinator owns domain operations."""
from __future__ import annotations

import sqlite3
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request

from app.core.errors import BusinessError, require
from app.core.validation import integer
from app.core.filters import guard_fields, integer_query

api = Blueprint("api", __name__, url_prefix="/api")


def market():
    return current_app.extensions["campus_market_service"]


def body() -> dict:
    result = request.get_json(silent=True)
    require(isinstance(result, dict), "INVALID_JSON", "请求体必须是 JSON 对象")
    return result


def token() -> str:
    header = request.headers.get("Authorization", "")
    require(header.startswith("Bearer ") and len(header) > 7,
            "UNAUTHORIZED", "需要 Authorization: Bearer <token>", 401)
    return header[7:].strip()


def login_required(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        g.actor = market().identity(token())
        return fn(*args, **kwargs)
    return decorated


def bounded_int_arg(name: str, default: int | None = None, minimum: int = 0) -> int | None:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise BusinessError("INVALID_QUERY", f"{name} 必须是整数") from exc
    return integer(value, name, minimum)


@api.get("/meta/state-transitions")
def state_transitions():
    from app.core.state_rules import state_catalog
    return state_catalog()


@api.get("/health")
def health():
    return {"status": "ok"}


@api.post("/auth/register")
def register():
    data = body()
    return jsonify(market().create_account(data.get("username"), data.get("password"))), 201


@api.post("/auth/login")
def login():
    data = body()
    return market().login(data.get("username"), data.get("password"))


@api.post("/auth/logout")
@login_required
def logout():
    return market().logout(token())


@api.get("/auth/me")
@login_required
def me():
    return g.actor


@api.get("/products")
def public_products():
    return {"items": market().list_public(
        keyword=request.args.get("keyword"),
        category=request.args.get("category"),
        min_price=bounded_int_arg("min_price"),
        max_price=bounded_int_arg("max_price"),
        availability=request.args.get("availability"),
        status=request.args.get("status"),
        limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0),
    )}


@api.get("/products/facets")
def product_facets():
    return market().public_facets()


@api.get("/products/count")
def product_count():
    return market().public_count(availability=request.args.get("availability"))


@api.get("/products/<int:product_id>")
def product_detail(product_id: int):
    return market().public_detail(product_id)


@api.get("/my/products")
@login_required
def my_products():
    return {"items": market().my_products(
        g.actor, status=request.args.get("status"),
        limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0))}


@api.get("/my/products/<int:product_id>")
@login_required
def my_product_detail(product_id):
    return market().my_product_detail(g.actor, product_id)


@api.get("/my/dashboard")
@login_required
def my_dashboard():
    return market().my_dashboard(g.actor)


@api.get("/my/products/<int:product_id>/reviews")
@login_required
def my_product_reviews(product_id):
    return {"items": market().review_history(g.actor, product_id)}


@api.get("/my/products/<int:product_id>/violations")
@login_required
def my_product_violations(product_id):
    return {"items": market().violation_history(g.actor, product_id)}


@api.post("/products")
@login_required
def new_product():
    return jsonify(market().create_product(g.actor, body())), 201


@api.patch("/products/<int:product_id>")
@login_required
def edit_product(product_id):
    return market().edit_product(g.actor, product_id, body())


@api.post("/products/<int:product_id>/submit")
@login_required
def submit_product(product_id):
    return market().submit_product(g.actor, product_id)


@api.post("/products/<int:product_id>/delist")
@login_required
def delist_product(product_id):
    return market().delist_product(g.actor, product_id)


@api.post("/admin/products/<int:product_id>/review")
@login_required
def admin_review(product_id):
    data = body()
    return market().review_product(g.actor, product_id, data.get("decision"), data.get("reason"))


@api.post("/admin/products/<int:product_id>/violation-delist")
@login_required
def violation_delist(product_id):
    return market().delist_product(g.actor, product_id, violation=True,
                                   reason=body().get("reason"))


@api.get("/admin/products/pending")
@login_required
def admin_pending_products():
    return {"items": market().review_queue(
        g.actor, category=request.args.get("category"),
        keyword=request.args.get("keyword"),
        limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0))}


@api.get("/admin/products/reviewed")
@login_required
def admin_reviewed_products():
    return {"items": market().reviewed_products(
        g.actor, decision=request.args.get("decision"),
        limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0))}


@api.get("/admin/products/workload")
@login_required
def admin_review_workload():
    return market().review_workload(g.actor)


@api.get("/admin/products/<int:product_id>")
@login_required
def admin_product_detail(product_id):
    return market().admin_product_detail(g.actor, product_id)


@api.get("/admin/products/<int:product_id>/reviews")
@login_required
def admin_product_reviews(product_id):
    from app.services.auth import require_admin
    require_admin(g.actor)
    return {"items": market().review_history(g.actor, product_id)}


@api.get("/admin/products/<int:product_id>/violations")
@login_required
def admin_product_violations(product_id):
    from app.services.auth import require_admin
    require_admin(g.actor)
    return {"items": market().violation_history(g.actor, product_id)}


@api.get("/admin/violations")
@login_required
def admin_violation_queue():
    return {"items": market().violation_queue(
        g.actor, status=request.args.get("status", "OPEN"),
        limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0))}


@api.post("/admin/products/<int:product_id>/violation-resolve")
@login_required
def admin_violation_resolve(product_id):
    data = guard_fields(body(), permitted={"handling_note"},
                        required={"handling_note"})
    return market().resolve_violation(g.actor, product_id, data["handling_note"])


@api.get("/admin/integrity")
@login_required
def admin_integrity_report():
    return market().integrity_report(
        g.actor, sample_size=bounded_int_arg("sample_size", 20, 1))


@api.get("/admin/integrity/products/<int:product_id>")
@login_required
def admin_product_diagnostics(product_id):
    return market().product_diagnostics(g.actor, product_id)


@api.post("/reservations")
@login_required
def reserve():
    data = body()
    return jsonify(market().reserve(
        g.actor, data.get("product_id"), data.get("request_id"),
        data.get("expected_version"), data.get("expected_price_cents"))), 201


@api.get("/reservations/mine")
@login_required
def my_reservations():
    return {"items": market().my_reservations(
        g.actor, status=request.args.get("status"),
        limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0))}


@api.get("/reservations/received")
@login_required
def received_reservations():
    return {"items": market().received_reservations(
        g.actor, status=request.args.get("status"),
        limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0))}


@api.get("/reservations/<int:reservation_id>")
@login_required
def reservation_detail(reservation_id):
    return market().private_reservation(g.actor, reservation_id)


@api.post("/reservations/<int:reservation_id>/confirm")
@login_required
def confirm(reservation_id):
    return market().confirm_reservation(g.actor, reservation_id)


@api.post("/reservations/<int:reservation_id>/reject")
@login_required
def reject(reservation_id):
    return market().reject_reservation(g.actor, reservation_id, body().get("reason"))


@api.post("/reservations/<int:reservation_id>/cancel")
@login_required
def cancel(reservation_id):
    return market().cancel_reservation(g.actor, reservation_id)


@api.post("/reservations/<int:reservation_id>/complete")
@login_required
def complete(reservation_id):
    return market().complete_reservation(g.actor, reservation_id)


@api.get("/stats/seller")
@login_required
def seller_stats():
    return market().seller_stats(g.actor)


@api.get("/admin/products/<int:product_id>/timeline")
@login_required
def admin_product_timeline(product_id):
    return {"items": market().admin_product_timeline(
        g.actor, product_id, limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0))}


@api.get("/admin/activity/summary")
@login_required
def admin_activity_summary():
    return market().admin_activity_summary(
        g.actor, start=request.args.get("start"), end=request.args.get("end"),
        limit=bounded_int_arg("limit", 20, 1))


@api.get("/admin/users/<int:user_id>/activity")
@login_required
def admin_user_activity(user_id):
    return market().admin_user_activity(
        g.actor, user_id, limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0))


@api.get("/admin/stats")
@login_required
def admin_stats():
    return market().admin_stats(g.actor, request.args.get("start"), request.args.get("end"))


@api.get("/admin/logs")
@login_required
def logs():
    return {"items": market().admin_logs(
        g.actor, limit=bounded_int_arg("limit", 50, 1),
        offset=bounded_int_arg("offset", 0),
        action=request.args.get("action"),
        actor_id=bounded_int_arg("actor_id", None, 1),
        start=request.args.get("start"), end=request.args.get("end"),
        target_type=request.args.get("target_type"),
        target_id=bounded_int_arg("target_id", None, 1),
        actor_query=request.args.get("actor_query"))}


def register_routes(app) -> None:
    app.register_blueprint(api)

    @app.errorhandler(BusinessError)
    def business_error(err: BusinessError):
        return {"error": {"code": err.code, "message": err.message}}, err.status

    @app.errorhandler(sqlite3.OperationalError)
    def database_unavailable(err: sqlite3.OperationalError):
        if "locked" in str(err).lower():
            return {"error": {"code": "DB_BUSY", "message": "数据库繁忙，请重试"}}, 503
        current_app.logger.exception("Unexpected database error")
        return {"error": {"code": "DB_ERROR", "message": "数据库操作失败"}}, 500
