from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


DEFAULT_TABLES = ["conversations", "messages", "file_assets", "codex_runs", "events"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the gateway SQLite database.")
    parser.add_argument("--db", default="data/gateway.sqlite3", help="SQLite database path.")
    parser.add_argument("--table", choices=DEFAULT_TABLES, help="Only print one table.")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tables = [args.table] if args.table else DEFAULT_TABLES
    for table in tables:
        print(f"\n## {table}")
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (args.limit,)).fetchall()
        for row in rows:
            print(json.dumps(dict(row), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

