from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


DEFAULT_TABLES = [
    "conversations",
    "messages",
    "file_assets",
    "codex_runs",
    "events",
    "run_auth_tokens",
]


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
            print(json.dumps(_display_row(table, row), ensure_ascii=False, indent=2))


def _display_row(table: str, row: sqlite3.Row) -> dict:
    data = dict(row)
    if table == "codex_runs":
        _expose_asset_mcp_fields(data)
    return data


def _expose_asset_mcp_fields(data: dict) -> None:
    metadata = _json_object(data.get("metadata_json"))
    command = _json_object(data.get("command_json"))
    asset_mcp = metadata.get("asset_mcp") if isinstance(metadata.get("asset_mcp"), dict) else {}
    command_asset_mcp = command.get("asset_mcp") if isinstance(command.get("asset_mcp"), dict) else {}
    data["asset_mcp_token"] = asset_mcp.get("token")
    data["asset_mcp_token_env_var"] = asset_mcp.get("token_env_var")
    data["asset_mcp_url"] = asset_mcp.get("url")
    data["asset_mcp_command_url"] = command_asset_mcp.get("url")
    data["asset_mcp_expires_at"] = asset_mcp.get("expires_at")


def _json_object(value: object) -> dict:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


if __name__ == "__main__":
    main()
