"""Application use-case coordinator: transaction boundaries and cross-module calls."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.errors import BusinessError, require
from app.core.validation import integer, string
from app.db.connection import initialize, read_connection, write_transaction
from app.services import activity, audit, auth, catalog, integrity, products, reservations, review_queue, reviews, violations
from app.db import read_models


class Market:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def initialize(self) -> None:
        initialize(self.db_path)

    def create_account(self, username: str, password: str) -> dict:
        with write_transaction(self.db_path) as conn:
            return auth.create_user(conn, username, password)

    def bootstrap_admin(self, username: str, password: str) -> dict:
        """Restricted to local CLI, never exposed as public HTTP endpoint."""
        with write_transaction(self.db_path) as conn:
            return auth.create_user(conn, username, password, admin=True)

    def login(self, username: str, password: str) -> dict:
        with write_transaction(self.db_path) as conn:
            token, actor = auth.issue_token(conn, username, password)
            return {"token": token, "user": actor}

    def logout(self, token: str) -> dict:
        with write_transaction(self.db_path) as conn:
            auth.revoke_token(conn, token)
            return {"ok": True}

    def identity(self, token: str) -> dict:
        with read_connection(self.db_path) as conn:
            return auth.resolve_token(conn, token)

    def create_product(self, actor: dict, fields: dict) -> dict:
        with write_transaction(self.db_path) as conn:
            return products.create_draft(conn, actor, fields)

    def edit_product(self, actor: dict, product_id: int, changes: dict) -> dict:
        with write_transaction(self.db_path) as conn:
            p = products.own_product(conn, product_id, actor)
            if p["status"] == "ON_SALE":
                return products.edit_on_sale_core(conn, actor, product_id, changes)
            return products.edit_draft(conn, actor, product_id, changes)

    def submit_product(self, actor: dict, product_id: int) -> dict:
        with write_transaction(self.db_path) as conn:
            return products.submit_review(conn, actor, product_id)

    def review_product(self, actor: dict, product_id: int,
                       decision: str, reason: str | None = None) -> dict:
        with write_transaction(self.db_path) as conn:
            return reviews.review(conn, actor, product_id, decision, reason)

    def delist_product(self, actor: dict, product_id: int,
                       violation: bool = False, reason: str | None = None) -> dict:
        # U7: authorize, update M2, cancel M4 reservations, append M6 log,
        # all on one connection and in one BEGIN IMMEDIATE transaction.
        with write_transaction(self.db_path) as conn:
            if violation:
                auth.require_admin(actor)
                reason = string(reason, "reason", limit=400)
            else:
                products.own_product(conn, product_id, actor)
            product = products.apply_delist(conn, product_id,
                                            violation=violation, reason=reason)
            cancelled = reservations.cancel_for_delist(
                conn, product_id, violation=violation, reason=reason)
            if violation:
                violations.open_case(conn, actor, product_id, reason)
            audit.log_success(conn, actor["id"],
                              "PRODUCT_VIOLATION_DELIST" if violation else "PRODUCT_DELIST",
                              "Product", product_id, reason)
            return {"product": product, "cancelled_reservations": cancelled}

    def reserve(self, actor: dict, product_id: int, request_id: str,
                expected_version: int, expected_price_cents: int) -> dict:
        try:
            with write_transaction(self.db_path) as conn:
                return reservations.create(conn, actor, product_id, request_id,
                                           expected_version, expected_price_cents)
        except sqlite3.IntegrityError as exc:
            # This is a last line of defense; the transaction-level check
            # normally classifies the reason before INSERT is attempted.
            raise BusinessError("RESERVATION_CONFLICT", "预约冲突，请刷新后重试", 409) from exc

    def confirm_reservation(self, actor: dict, reservation_id: int) -> dict:
        with write_transaction(self.db_path) as conn:
            return reservations.confirm(conn, actor, reservation_id)

    def reject_reservation(self, actor: dict, reservation_id: int, reason: str) -> dict:
        with write_transaction(self.db_path) as conn:
            return reservations.reject(conn, actor, reservation_id, reason)

    def cancel_reservation(self, actor: dict, reservation_id: int) -> dict:
        with write_transaction(self.db_path) as conn:
            return reservations.cancel(conn, actor, reservation_id)

    def complete_reservation(self, actor: dict, reservation_id: int) -> dict:
        # U4/U5: M4 completion and M2 sale share one atomic transaction.
        with write_transaction(self.db_path) as conn:
            booking, product_id = reservations.complete_booking(conn, actor, reservation_id)
            product = products.mark_sold(conn, product_id)
            audit.log_success(conn, actor["id"],
                              "RESERVATION_COMPLETE", "Reservation", reservation_id)
            return {"reservation": booking, "product": product}

    def list_public(self, **filters) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return catalog.visible_products(conn, **filters)

    def public_facets(self) -> dict:
        with read_connection(self.db_path) as conn:
            return catalog.public_facets(conn)

    def public_count(self, *, availability=None) -> dict:
        with read_connection(self.db_path) as conn:
            return {"count": catalog.public_product_count(conn, availability=availability)}

    def public_detail(self, product_id: int) -> dict:
        with read_connection(self.db_path) as conn:
            p = products.product_row(conn, product_id)
            require(p["status"] == "ON_SALE", "NOT_FOUND", "商品不存在或未公开", 404)
            p["booked"] = products.active_booking(conn, product_id)
            # Expose only the public account name; never serialize the user
            # row (password_hash, session tokens and other private fields).
            seller = conn.execute(
                "SELECT username FROM users WHERE id=?", (p["seller_id"],)
            ).fetchone()
            p["seller_name"] = seller["username"] if seller else ""
            return p

    def my_products(self, actor: dict, *, status=None, limit=50, offset=0) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return read_models.owned_products(conn, actor, status=status,
                                              limit=limit, offset=offset)

    def my_product_detail(self, actor: dict, product_id: int) -> dict:
        with read_connection(self.db_path) as conn:
            return read_models.owned_product_detail(conn, actor, product_id)

    def my_reservations(self, actor: dict, *, status=None, limit=50, offset=0) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return read_models.buyer_reservations(conn, actor, status=status,
                                                   limit=limit, offset=offset)

    def received_reservations(self, actor: dict, *, status=None, limit=50, offset=0) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return read_models.seller_reservations(conn, actor, status=status,
                                                    limit=limit, offset=offset)

    def private_reservation(self, actor: dict, reservation_id: int) -> dict:
        with read_connection(self.db_path) as conn:
            return read_models.reservation_detail(conn, actor, reservation_id)

    def my_dashboard(self, actor: dict) -> dict:
        with read_connection(self.db_path) as conn:
            return {"products": read_models.count_owned_products(conn, actor),
                    "reservations": read_models.count_reservations(conn, actor)}

    def review_history(self, actor: dict, product_id: int) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return read_models.review_history(conn, actor, product_id)

    def review_queue(self, actor: dict, *, category=None, keyword=None,
                     limit=50, offset=0) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return review_queue.search_pending(conn, actor, category=category,
                                               keyword=keyword, limit=limit, offset=offset)

    def admin_product_detail(self, actor: dict, product_id: int) -> dict:
        with read_connection(self.db_path) as conn:
            return review_queue.admin_product_detail(conn, actor, product_id)

    def reviewed_products(self, actor: dict, *, decision: str,
                          limit: int = 50, offset: int = 0) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return review_queue.search_reviewed(conn, actor, decision=decision,
                                                limit=limit, offset=offset)

    def review_workload(self, actor: dict) -> dict:
        with read_connection(self.db_path) as conn:
            return review_queue.review_workload(conn, actor)

    def resolve_violation(self, actor: dict, product_id: int, handling_note: str) -> dict:
        with write_transaction(self.db_path) as conn:
            return violations.resolve(conn, actor, product_id, handling_note)

    def violation_queue(self, actor: dict, *, status="OPEN", limit=50, offset=0) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return violations.list_cases(conn, actor, status=status, limit=limit, offset=offset)

    def violation_history(self, actor: dict, product_id: int) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return violations.product_cases(conn, actor, product_id)

    def integrity_report(self, actor: dict, *, sample_size=20) -> dict:
        with read_connection(self.db_path) as conn:
            return integrity.run_checks(conn, actor, sample_size=sample_size)

    def product_diagnostics(self, actor: dict, product_id: int) -> dict:
        auth.require_admin(actor)
        with read_connection(self.db_path) as conn:
            return integrity.quick_product_check(conn, product_id)

    def admin_product_timeline(self, actor: dict, product_id: int,
                               *, limit=50, offset=0) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return activity.product_timeline(conn, actor, product_id,
                                             limit=limit, offset=offset)

    def admin_activity_summary(self, actor: dict, *, start=None, end=None,
                               limit=20) -> dict:
        with read_connection(self.db_path) as conn:
            return activity.activity_summary(conn, actor, start=start,
                                              end=end, limit=limit)

    def admin_user_activity(self, actor: dict, user_id: int,
                            *, limit=50, offset=0) -> dict:
        with read_connection(self.db_path) as conn:
            return activity.user_activity(conn, actor, user_id,
                                          limit=limit, offset=offset)

    def seller_stats(self, actor: dict) -> dict:
        with read_connection(self.db_path) as conn:
            return catalog.seller_statistics(conn, actor)

    def admin_stats(self, actor: dict, start=None, end=None) -> dict:
        with read_connection(self.db_path) as conn:
            return catalog.platform_statistics(conn, actor, start, end)

    def admin_logs(self, actor: dict, limit=50, offset=0,
                   action=None, actor_id=None, start=None, end=None,
                   target_type=None, target_id=None, actor_query=None) -> list[dict]:
        with read_connection(self.db_path) as conn:
            return audit.query_logs(conn, actor, limit=limit, offset=offset,
                                    action=action, actor_id=actor_id,
                                    start=start, end=end,
                                    target_type=target_type, target_id=target_id,
                                    actor_query=actor_query)
