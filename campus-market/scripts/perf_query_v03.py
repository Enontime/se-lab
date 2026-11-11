"""REQ-018 benchmark: 10k products, 20 concurrent HTTP clients, >=95% <=2s.

Examples:
  python -m scripts.perf_query_v03 --mode service --requests 400
  python -m scripts.perf_query_v03 --mode http --requests 1000 --output perf_results.json

The 'service' mode only profiles M5+SQLite and is NOT full HTTP acceptance.
HTTP mode launches an ephemeral local Flask server with a temporary SQLite DB.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import sqlite3
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from app.services.market import Market
from app.db.connection import write_transaction


def seed(market: Market, items: int) -> None:
    t = datetime.now(timezone.utc).isoformat()
    with write_transaction(market.db_path) as conn:
        cur = conn.execute("""INSERT INTO users
            (username,username_key,password_hash,account_type,created_at)
            VALUES('performance_user','performance_user','BENCHMARK_ONLY','NORMAL',?)""", (t,))
        seller_id = cur.lastrowid
        conn.executemany("""INSERT INTO products
            (seller_id,title,category,price_cents,item_condition,description,
             trade_location,status,version,created_at,updated_at,published_at)
            VALUES(?,?,?,?,?,?,?,'ON_SALE',1,?,?,?)""",
            ((seller_id, f"教材与商品 {i}", "教材" if i % 2 else "数码",
              100 + i % 10000, "九成新", f"商品描述编号{i}", "校内", t, t, t)
             for i in range(items)),
        )


def measure(worker_count: int, total_requests: int, request_func):
    assert total_requests % worker_count == 0
    barrier = threading.Barrier(worker_count)
    per_worker = total_requests // worker_count
    def one_worker(index):
        barrier.wait(timeout=30)
        records = []
        for j in range(per_worker):
            case = (index + j) % 4
            t0 = time.perf_counter()
            try:
                request_func(case)
                error = None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            records.append((time.perf_counter() - t0, error))
        return records
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        data = [item for group in pool.map(one_worker, range(worker_count)) for item in group]
    wall = time.perf_counter() - started
    return data, wall


def main() -> None:
    parser = argparse.ArgumentParser(description="REQ-018 query performance benchmark")
    parser.add_argument("--mode", choices=["service", "http"], default="http")
    parser.add_argument("--items", type=int, default=10000)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--threshold", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=Path("perf_query_results.json"))
    args = parser.parse_args()
    if args.items <= 0 or args.workers <= 0 or args.requests <= 0 or args.requests % args.workers:
        parser.error("items/workers/requests must be >0 and requests must be divisible by workers")
    with tempfile.TemporaryDirectory(prefix="campus-market-perf-") as tempdir:
        db_path = Path(tempdir) / "perf.sqlite3"
        market = Market(db_path)
        market.initialize()
        t = time.perf_counter()
        seed(market, args.items)
        seed_seconds = time.perf_counter() - t
        assert market.list_public(limit=1), "no seeded products"
        cases = [
            dict(limit=50),
            dict(category="教材", limit=50),
            dict(keyword="商品", min_price=1000, max_price=5000, limit=50),
            dict(status="AVAILABLE", limit=50),
        ]
        stop_server = None
        if args.mode == "service":
            request_func = lambda i: market.list_public(**cases[i])
        else:
            from app import create_app
            from werkzeug.serving import WSGIRequestHandler, make_server
            from urllib.parse import urlencode
            app = create_app({"DATABASE": str(db_path), "TESTING": False})
            class QuietHandler(WSGIRequestHandler):
                def log(self, type, message, *args):
                    pass  # Avoid logging 1000 identical benchmark requests to console.

            server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
            port = server.server_port
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stop_server = lambda: (server.shutdown(), thread.join(timeout=10))
            paths = [f"http://127.0.0.1:{port}/api/products?{urlencode(q)}" for q in cases]
            def request_func(i):
                with urlopen(paths[i], timeout=10) as r:
                    if r.status != 200:
                        raise RuntimeError(f"HTTP {r.status}")
                    response = json.load(r)
                    if "items" not in response:
                        raise RuntimeError("missing items")
        try:
            # One cold/warm-up sample per query type; excluded from timed results.
            for i in range(4):
                request_func(i)
            results, wall = measure(args.workers, args.requests, request_func)
        finally:
            if stop_server:
                stop_server()
        latencies = sorted(seconds for seconds, error in results if error is None)
        errors = [error for _, error in results if error]
        success_in_threshold = sum(error is None and seconds <= args.threshold for seconds, error in results)
        fraction = success_in_threshold / len(results)
        summary = {
            "mode": args.mode,
            "criterion": "REQ-018 only if mode=http and setup matches agreed environment",
            "items": args.items,
            "concurrent_users": args.workers,
            "total_requests": args.requests,
            "seed_seconds": round(seed_seconds, 3),
            "measurement_wall_seconds": round(wall, 3),
            "failures": len(errors),
            "first_errors": errors[:5],
            "p50_seconds": round(statistics.median(latencies), 4) if latencies else None,
            "p95_seconds": round(latencies[max(0, int(len(latencies)*0.95+0.9999)-1)], 4) if latencies else None,
            "max_seconds": round(latencies[-1], 4) if latencies else None,
            "threshold_seconds": args.threshold,
            "within_threshold_count": success_in_threshold,
            "within_threshold_ratio": round(fraction, 5),
            "criterion_met": fraction >= 0.95 and not errors,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "flask": importlib.metadata.version("flask") if args.mode == "http" else "not used",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Saved {args.output.resolve()}")
        if errors or fraction < 0.95:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
