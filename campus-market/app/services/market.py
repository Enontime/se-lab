"""Application use-case coordinator: transaction boundaries and cross-module calls."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.errors import BusinessError, require
from app.core.validation import string
from app.db import read_models
from app.db.connection import initialize, read_connection, write_transaction
from app.services import (
    activity,
    audit,
    auth,
    catalog,
    integrity,
    products,
    reservations,
    review_queue,
    reviews,
    violations,
)


class Market:
    """Coordinate use cases and own their SQLite connection and transaction boundaries."""
    def __init__(self, db_path: str | Path):
        """Store the path used when opening the database for later operations."""
        self.db_path = str(db_path)

    def initialize(self) -> None:
        """Prepare the database schema and apply supported migrations."""
        initialize(self.db_path)

    def create_account(self, username: str, password: str) -> dict:
        """Create a normal account and return its public identity."""
        with write_transaction(self.db_path) as conn:
            return auth.create_user(conn, username, password)

    def bootstrap_admin(self, username: str, password: str) -> dict:
        """Restricted to local CLI, never exposed as public HTTP endpoint."""
        with write_transaction(self.db_path) as conn:
            return auth.create_user(conn, username, password, admin=True)

    def login(self, username: str, password: str) -> dict:
        """Issue a bearer token and return it with the authenticated account."""
        with write_transaction(self.db_path) as conn:
            token, actor = auth.issue_token(conn, username, password)
            return {"token": token, "user": actor}

    def logout(self, token: str) -> dict:
        """Revoke the supplied bearer session and return a success marker."""
        with write_transaction(self.db_path) as conn:
            auth.revoke_token(conn, token)
            return {"ok": True}

    def identity(self, token: str) -> dict:
        """Resolve a live bearer token to its actor or raise UNAUTHORIZED."""
        with read_connection(self.db_path) as conn:
            return auth.resolve_token(conn, token)

    def create_product(self, actor: dict, fields: dict) -> dict:
        """Create a draft for a normal seller and return its stored fields."""
        with write_transaction(self.db_path) as conn:
            return products.create_draft(conn, actor, fields)

    def edit_product(self, actor: dict, product_id: int, changes: dict) -> dict:
        """Apply owner edits under the rules for on-sale and directly editable products."""
        with write_transaction(self.db_path) as conn:
            p = products.own_product(conn, product_id, actor)
            if p["status"] == "ON_SALE":
                return products.edit_on_sale_core(conn, actor, product_id, changes)
            return products.edit_draft(conn, actor, product_id, changes)

    def submit_product(self, actor: dict, product_id: int) -> dict:
        """Move the seller's complete, eligible product to pending review."""
        with write_transaction(self.db_path) as conn:
            return products.submit_review(conn, actor, product_id)

    def review_product(self, actor: dict, product_id: int,
                       decision: str, reason: str | None = None) -> dict:
        """Apply an admin decision and persist the review and audit record atomically."""
        with write_transaction(self.db_path) as conn:
            return reviews.review(conn, actor, product_id, decision, reason)

    def delist_product(self, actor: dict, product_id: int,
                       violation: bool = False, reason: str | None = None) -> dict:
        # U7: authorize, update M2, cancel M4 reservations, append M6 log,
        # all on one connection and in one BEGIN IMMEDIATE transaction.
        """Delist an on-sale product and cancel its active bookings atomically.

        The owner may withdraw it, or an admin may delist it for a violation
        with a reason. Return the product and the number of cancelled bookings.
        """
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
        """Create or replay a booking using the buyer's request key and displayed snapshot.

        The write transaction serializes competing bookings. Return the booking
        and a replay flag, or report a stale snapshot or reservation conflict.
        """
        try:
            with write_transaction(self.db_path) as conn:
                return reservations.create(conn, actor, product_id, request_id,
                                           expected_version, expected_price_cents)
        except sqlite3.IntegrityError as exc:
            # This is a last line of defense; the transaction-level check
            # normally classifies the reason before INSERT is attempted.
            raise BusinessError("RESERVATION_CONFLICT", "预约冲突，请刷新后重试", 409) from exc

    def confirm_reservation(self, actor: dict, reservation_id: int) -> dict:
        """Confirm the seller's pending reservation for an on-sale product."""
        with write_transaction(self.db_path) as conn:
            return reservations.confirm(conn, actor, reservation_id)

    def reject_reservation(self, actor: dict, reservation_id: int, reason: str) -> dict:
        """Cancel the seller's pending on-sale reservation with a rejection reason."""
        with write_transaction(self.db_path) as conn:
            return reservations.reject(conn, actor, reservation_id, reason)

    def cancel_reservation(self, actor: dict, reservation_id: int) -> dict:
        """Cancel a pending or confirmed reservation owned by the buyer."""
        with write_transaction(self.db_path) as conn:
            return reservations.cancel(conn, actor, reservation_id)

    def complete_reservation(self, actor: dict, reservation_id: int) -> dict:
        # U4/U5: M4 completion and M2 sale share one atomic transaction.
        """Complete the seller's confirmed booking and mark its product sold atomically."""
        with write_transaction(self.db_path) as conn:
            booking, product_id = reservations.complete_booking(conn, actor, reservation_id)
            product = products.mark_sold(conn, product_id)
            audit.log_success(conn, actor["id"],
                              "RESERVATION_COMPLETE", "Reservation", reservation_id)
            return {"reservation": booking, "product": product}

    def list_public(self, **filters) -> list[dict]:
        """Return the on-sale catalog page matching the supplied filters."""
        with read_connection(self.db_path) as conn:
            return catalog.visible_products(conn, **filters)

    def public_facets(self) -> dict:
        """Return aggregate category, price, and availability data for on-sale products."""
        with read_connection(self.db_path) as conn:
            return catalog.public_facets(conn)

    def public_count(self, *, availability=None) -> dict:
        """Return the on-sale product count for the given availability filter."""
        with read_connection(self.db_path) as conn:
            return {"count": catalog.public_product_count(conn, availability=availability)}

    def public_detail(self, product_id: int) -> dict:
        """Return a public product with its booking flag and the seller's display name."""
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
        """Page through the actor's own products across private and public states."""
        with read_connection(self.db_path) as conn:
            return read_models.owned_products(conn, actor, status=status,
                                              limit=limit, offset=offset)

    def my_product_detail(self, actor: dict, product_id: int) -> dict:
        """Return an owned product, including private states and its booking flag."""
        with read_connection(self.db_path) as conn:
            return read_models.owned_product_detail(conn, actor, product_id)

    def my_reservations(self, actor: dict, *, status=None, limit=50, offset=0) -> list[dict]:
        """Page through reservations made by the current buyer."""
        with read_connection(self.db_path) as conn:
            return read_models.buyer_reservations(conn, actor, status=status,
                                                   limit=limit, offset=offset)

    def received_reservations(self, actor: dict, *, status=None, limit=50, offset=0) -> list[dict]:
        """Page through reservations for products owned by the current seller."""
        with read_connection(self.db_path) as conn:
            return read_models.seller_reservations(conn, actor, status=status,
                                                    limit=limit, offset=offset)

    def private_reservation(self, actor: dict, reservation_id: int) -> dict:
        """Return a booking only to its buyer or the linked product's seller."""
        with read_connection(self.db_path) as conn:
            return read_models.reservation_detail(conn, actor, reservation_id)

    def my_dashboard(self, actor: dict) -> dict:
        """Return the actor's product counts and separate buyer and seller booking counts."""
        with read_connection(self.db_path) as conn:
            return {"products": read_models.count_owned_products(conn, actor),
                    "reservations": read_models.count_reservations(conn, actor)}

    def review_history(self, actor: dict, product_id: int) -> list[dict]:
        """Return a product's review history to its seller or an administrator."""
        with read_connection(self.db_path) as conn:
            return read_models.review_history(conn, actor, product_id)

    def review_queue(self, actor: dict, *, category=None, keyword=None,
                     limit=50, offset=0) -> list[dict]:
        """Return the filtered, paginated pending review queue for an administrator."""
        with read_connection(self.db_path) as conn:
            return review_queue.search_pending(conn, actor, category=category,
                                               keyword=keyword, limit=limit, offset=offset)

    def admin_product_detail(self, actor: dict, product_id: int) -> dict:
        """Return a product's moderation detail to an administrator."""
        with read_connection(self.db_path) as conn:
            return review_queue.admin_product_detail(conn, actor, product_id)

    def reviewed_products(self, actor: dict, *, decision: str,
                          limit: int = 50, offset: int = 0) -> list[dict]:
        """Page through products matching an administrator's review decision filter."""
        with read_connection(self.db_path) as conn:
            return review_queue.search_reviewed(conn, actor, decision=decision,
                                                limit=limit, offset=offset)

    def review_workload(self, actor: dict) -> dict:
        """Return review workload counts for an administrator."""
        with read_connection(self.db_path) as conn:
            return review_queue.review_workload(conn, actor)

    def resolve_violation(self, actor: dict, product_id: int, handling_note: str) -> dict:
        """Record an administrator's resolution and return the updated case and product."""
        with write_transaction(self.db_path) as conn:
            return violations.resolve(conn, actor, product_id, handling_note)

    def violation_queue(self, actor: dict, *, status="OPEN", limit=50, offset=0) -> list[dict]:
        """Page through administrator-visible violation cases by case status."""
        with read_connection(self.db_path) as conn:
            return violations.list_cases(conn, actor, status=status, limit=limit, offset=offset)

    def violation_history(self, actor: dict, product_id: int) -> list[dict]:
        """Return a product's violation cases to its seller or an administrator."""
        with read_connection(self.db_path) as conn:
            return violations.product_cases(conn, actor, product_id)

    def integrity_report(self, actor: dict, *, sample_size=20) -> dict:
        """Return read-only integrity findings with bounded samples for an administrator."""
        with read_connection(self.db_path) as conn:
            return integrity.run_checks(conn, actor, sample_size=sample_size)

    def product_diagnostics(self, actor: dict, product_id: int) -> dict:
        """Check booking and product state consistency for an administrator."""
        auth.require_admin(actor)
        with read_connection(self.db_path) as conn:
            return integrity.quick_product_check(conn, product_id)

    def admin_product_timeline(self, actor: dict, product_id: int,
                               *, limit=50, offset=0) -> list[dict]:
        """Return paginated audit events for a product and its bookings to an admin."""
        with read_connection(self.db_path) as conn:
            return activity.product_timeline(conn, actor, product_id,
                                             limit=limit, offset=offset)

    def admin_activity_summary(self, actor: dict, *, start=None, end=None,
                               limit=20) -> dict:
        """Summarize audit events by action and day for an administrator."""
        with read_connection(self.db_path) as conn:
            return activity.activity_summary(conn, actor, start=start,
                                              end=end, limit=limit)

    def admin_user_activity(self, actor: dict, user_id: int,
                            *, limit=50, offset=0) -> dict:
        """Return an account and a page of its audit events to an administrator."""
        with read_connection(self.db_path) as conn:
            return activity.user_activity(conn, actor, user_id,
                                          limit=limit, offset=offset)

    def seller_stats(self, actor: dict) -> dict:
        """Return the current seller's product and completed transaction counts."""
        with read_connection(self.db_path) as conn:
            return catalog.seller_statistics(conn, actor)

    def admin_stats(self, actor: dict, start=None, end=None) -> dict:
        """Return platform publication and sales counts for an administrator."""
        with read_connection(self.db_path) as conn:
            return catalog.platform_statistics(conn, actor, start, end)

    def admin_logs(self, actor: dict, limit=50, offset=0,
                   action=None, actor_id=None, start=None, end=None,
                   target_type=None, target_id=None, actor_query=None) -> list[dict]:
        """Return the administrator's audit page filtered by actor, action, target, or date."""
        with read_connection(self.db_path) as conn:
            return audit.query_logs(conn, actor, limit=limit, offset=offset,
                                    action=action, actor_id=actor_id,
                                    start=start, end=end,
                                    target_type=target_type, target_id=target_id,
                                    actor_query=actor_query)
