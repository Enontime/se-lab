"""Bootstrap an admin locally; never expose admin registration in public API."""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from app.services.market import Market


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--db", default=str(Path("instance") / "campus_market.sqlite3"))
    args = parser.parse_args()
    password = getpass.getpass("管理员密码(至少8字符): ")
    service = Market(args.db)
    service.initialize()
    actor = service.bootstrap_admin(args.username, password)
    print(f"管理员已创建: {actor['username']} (id={actor['id']})")


if __name__ == "__main__":
    main()
