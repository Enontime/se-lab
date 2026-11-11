"""Forward-only, repeatable data migration for the v0.3 support table.

No old tables are rebuilt or truncated. The database can be opened by the old
v0.2 app before this upgrade; this routine reconstructs unresolved violation
cases from the original business log and product status.
"""
from __future__ import annotations


def ensure_reservation_snapshot_columns(conn) -> list[str]:
    """Upgrade existing v0.2 databases without overwriting historical bookings.

    Legacy reservations keep NULL snapshots because the original value at the
    time of booking is no longer provable from a potentially edited product.
    Newly created bookings write both snapshot columns atomically.
    """
    fields = {row["name"] for row in conn.execute("PRAGMA table_info(reservations)")}
    added = []
    for column in ("expected_version", "expected_price_cents"):
        if column not in fields:
            conn.execute("ALTER TABLE reservations ADD COLUMN " + column + " INTEGER")
            added.append(column)
    return added


def backfill_open_violation_cases(conn) -> int:
    """Create a case for each pre-v0.3 currently violating product.

    Only use the original violation-delisting audit log as administrator proof.
    If an inconsistent database lacks that log, do not invent an operator.
    """
    statement = """INSERT INTO violation_cases(product_id,flagged_by,reason,created_at)
        SELECT p.id,l.actor_id,
               COALESCE(NULLIF(p.violation_reason,''),l.reason,'历史违规下架'),
               l.created_at
        FROM products p
        JOIN operation_logs l ON l.id=(
            SELECT x.id FROM operation_logs x
            WHERE x.target_type='Product' AND x.target_id=p.id
              AND x.action='PRODUCT_VIOLATION_DELIST'
            ORDER BY x.id DESC LIMIT 1
        )
        WHERE p.status='VIOLATION_DELISTED'
          AND NOT EXISTS (
              SELECT 1 FROM violation_cases c WHERE c.product_id=p.id
          )"""
    return conn.execute(statement).rowcount


def migration_status(conn) -> dict:
    """Small verification summary; a missing historical audit event is visible."""
    untracked = conn.execute("""SELECT COUNT(*) FROM products p
        WHERE p.status='VIOLATION_DELISTED' AND NOT EXISTS(
            SELECT 1 FROM violation_cases c WHERE c.product_id=p.id)""").fetchone()[0]
    open_cases = conn.execute("""SELECT COUNT(*) FROM violation_cases
        WHERE resolved_at IS NULL""").fetchone()[0]
    resolved = conn.execute("""SELECT COUNT(*) FROM violation_cases
        WHERE resolved_at IS NOT NULL""").fetchone()[0]
    return {"untracked_violations": untracked,
            "open_cases": open_cases, "resolved_cases": resolved}
