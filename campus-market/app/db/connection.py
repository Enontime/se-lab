"""A connection-per-operation SQLite access layer with explicit write ownership."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from app.db.migrations import backfill_open_violation_cases, ensure_reservation_snapshot_columns

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def initialize(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("BEGIN IMMEDIATE")
        try:
            ensure_reservation_snapshot_columns(conn)
            backfill_open_violation_cases(conn)
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
    finally:
        conn.close()


@contextmanager
def read_connection(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_transaction(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """SQLite write serialization, replacing UML's FOR UPDATE statement.

    All state-changing business operations must enter BEGIN IMMEDIATE before
    reading state they will change. Repository helpers never commit.
    """
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
    finally:
        conn.close()
