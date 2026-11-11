"""Read-only invariants audit for REQ-012, REQ-015 and REQ-020.

Database constraints catch most issues at write time, while these checks can
identify inconsistent data introduced by manual SQL or outdated application
versions. Checking does not repair or modify records silently.
"""
from __future__ import annotations

from app.services.auth import require_admin
from app.db.migrations import migration_status
from app.core.validation import fingerprint

CHECKS = (
    (
        "MULTIPLE_ACTIVE_BOOKINGS",
        """SELECT product_id AS id FROM reservations
           WHERE status IN ('PENDING','CONFIRMED')
           GROUP BY product_id HAVING COUNT(*)>1""",
    ),
    (
        "ACTIVE_BOOKING_FOR_NONSALE",
        """SELECT r.id FROM reservations r JOIN products p ON p.id=r.product_id
           WHERE r.status IN ('PENDING','CONFIRMED') AND p.status<>'ON_SALE'""",
    ),
    (
        "SOLD_WITHOUT_COMPLETED",
        """SELECT p.id FROM products p WHERE p.status='SOLD'
           AND NOT EXISTS (SELECT 1 FROM reservations r WHERE r.product_id=p.id
                           AND r.status='COMPLETED')""",
    ),
    (
        "COMPLETED_PRODUCT_NOT_SOLD",
        """SELECT r.id FROM reservations r JOIN products p ON p.id=r.product_id
           WHERE r.status='COMPLETED' AND p.status<>'SOLD'""",
    ),
    (
        "MULTIPLE_COMPLETED_FOR_PRODUCT",
        """SELECT product_id AS id FROM reservations WHERE status='COMPLETED'
           GROUP BY product_id HAVING COUNT(*)>1""",
    ),
    (
        "BOOKING_SELF_PURCHASE",
        """SELECT r.id FROM reservations r JOIN products p ON p.id=r.product_id
           WHERE r.buyer_id=p.seller_id""",
    ),
    (
        "CANCELLED_WITHOUT_REASON",
        """SELECT id FROM reservations WHERE status='CANCELLED'
           AND (cancel_source IS NULL OR cancelled_at IS NULL)""",
    ),
    (
        "REVIEW_INCONSISTENT_ACCOUNT",
        """SELECT h.id FROM product_reviews h JOIN users u ON u.id=h.admin_id
           WHERE u.account_type<>'ADMIN'""",
    ),
    (
        "VIOLATION_CASE_FLAGGER_NOT_ADMIN",
        """SELECT c.id FROM violation_cases c JOIN users u ON u.id=c.flagged_by
           WHERE u.account_type<>'ADMIN'""",
    ),
    (
        "VIOLATION_CASE_RESOLVER_NOT_ADMIN",
        """SELECT c.id FROM violation_cases c JOIN users u ON u.id=c.resolved_by
           WHERE u.account_type<>'ADMIN'""",
    ),
    (
        "OPEN_CASE_WITHOUT_VIOLATION_STATE",
        """SELECT c.id FROM violation_cases c JOIN products p ON p.id=c.product_id
           WHERE c.resolved_at IS NULL AND p.status<>'VIOLATION_DELISTED'""",
    ),
    (
        "VIOLATION_WITHOUT_CASE",
        """SELECT p.id FROM products p WHERE p.status='VIOLATION_DELISTED'
           AND NOT EXISTS(SELECT 1 FROM violation_cases c
                          WHERE c.product_id=p.id)""",
    ),
)


def run_checks(conn, actor: dict, *, sample_size: int = 20) -> dict:
    """Return totals and bounded IDs; no private data is returned to non-admins."""
    from app.core.validation import integer
    from app.core.errors import require

    require_admin(actor)
    sample_size = integer(sample_size, "sample_size", 1)
    require(sample_size <= 100, "INVALID_INPUT", "sample_size 不能超过 100")
    problems = []
    fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_issues:
        problems.append({"code": "FOREIGN_KEY_VIOLATION", "count": len(fk_issues),
                         "sample_ids": [dict(row) for row in fk_issues[:sample_size]]})
    for code, sql in CHECKS:
        # Execute the same predicate for the count and the bounded sample.
        # SQL and code identifiers are fixed program constants, never input.
        count = conn.execute("SELECT COUNT(*) FROM (" + sql + ")").fetchone()[0]
        if count:
            rows = conn.execute("SELECT id FROM (" + sql + ") ORDER BY id LIMIT ?",
                                (sample_size,)).fetchall()
            problems.append({"code": code, "count": count,
                             "sample_ids": [row["id"] for row in rows]})
    snapshots = check_request_snapshots(conn, sample_size=sample_size)
    if snapshots["count"]:
        problems.append({"code": "REQUEST_SNAPSHOT_MISMATCH", **snapshots})
    return {"ok": not problems, "issues": problems,
            "checked_rules": len(CHECKS) + 1,
            "migration": migration_status(conn)}


def quick_product_check(conn, product_id: int) -> dict:
    """Domain-relevant detail for diagnosing a specific product safely in CLI."""
    from app.core.validation import integer
    from app.services.products import product_row

    product_id = integer(product_id, "product_id")
    p = product_row(conn, product_id)
    counts = {row["status"]: row["n"] for row in conn.execute(
        """SELECT status,COUNT(*) AS n FROM reservations
           WHERE product_id=? GROUP BY status""", (product_id,))}
    active = counts.get("PENDING", 0) + counts.get("CONFIRMED", 0)
    complete = counts.get("COMPLETED", 0)
    valid = active <= 1 and (p["status"] == "ON_SALE" or active == 0)
    valid = valid and (p["status"] == "SOLD") == bool(complete)
    return {"product_id": product_id, "product_status": p["status"],
            "reservation_counts": counts, "invariants_ok": valid}


def check_request_snapshots(conn, *, sample_size: int = 20) -> dict:
    """Check stored request keys and immutable parameter fingerprints.

    Pre-v0.3 records legitimately have NULL snapshots and cannot be checked;
    never infer their original price from the current product's price.
    """
    mismatched = []
    count = 0
    legacy_count = 0
    verified_count = 0
    for row in conn.execute("""SELECT id,product_id,expected_version,
        expected_price_cents,request_fingerprint FROM reservations"""):
        version, cents = row["expected_version"], row["expected_price_cents"]
        if version is None and cents is None:
            legacy_count += 1
            continue
        verified_count += 1
        invalid = (version is None or cents is None or version < 1 or cents < 0)
        if not invalid:
            expected = fingerprint(row["product_id"], version, cents)
            invalid = expected != row["request_fingerprint"]
        if invalid:
            count += 1
            if len(mismatched) < sample_size:
                mismatched.append(row["id"])
    return {"count": count, "sample_ids": mismatched,
            "verified_records": verified_count, "legacy_records": legacy_count}
