from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import secrets
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from .ids import now_iso, short_id

TOKEN_PURPOSE_ASSET_MCP = "asset_mcp"
DEFAULT_TOKEN_TTL_HOURS = 24


class RunAuthService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_token(
        self,
        *,
        run_id: str,
        purpose: str = TOKEN_PURPOSE_ASSET_MCP,
        ttl_hours: int = DEFAULT_TOKEN_TTL_HOURS,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        token_id = short_id("tok")
        created_at = now_iso()
        expires_at = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO run_auth_tokens
            (token_id, run_id, token_hash, purpose, status, expires_at, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token_id,
                run_id,
                _hash_token(token),
                purpose,
                "active",
                expires_at,
                created_at,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return {
            "token_id": token_id,
            "run_id": run_id,
            "token": token,
            "purpose": purpose,
            "status": "active",
            "expires_at": expires_at,
            "created_at": created_at,
            "metadata": metadata or {},
        }

    def verify_token(self, token: str, *, purpose: str = TOKEN_PURPOSE_ASSET_MCP) -> dict[str, Any] | None:
        if not token:
            return None
        row = self.conn.execute(
            """
            SELECT * FROM run_auth_tokens
            WHERE token_hash = ?
              AND purpose = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (_hash_token(token), purpose),
        ).fetchone()
        if row is None:
            return None
        record = self._row_to_token(row)
        if record["status"] != "active":
            return None
        if _is_expired(record["expires_at"]):
            self.conn.execute(
                "UPDATE run_auth_tokens SET status = ?, last_used_at = ? WHERE token_id = ?",
                ("expired", now_iso(), record["token_id"]),
            )
            self.conn.commit()
            return None
        self.conn.execute(
            "UPDATE run_auth_tokens SET last_used_at = ? WHERE token_id = ?",
            (now_iso(), record["token_id"]),
        )
        self.conn.commit()
        return record

    def revoke_run_tokens(self, run_id: str, *, purpose: str = TOKEN_PURPOSE_ASSET_MCP) -> int:
        cursor = self.conn.execute(
            """
            UPDATE run_auth_tokens
            SET status = ?, last_used_at = ?
            WHERE run_id = ?
              AND purpose = ?
              AND status = 'active'
            """,
            ("revoked", now_iso(), run_id, purpose),
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def _row_to_token(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "token_id": row["token_id"],
            "run_id": row["run_id"],
            "purpose": row["purpose"],
            "status": row["status"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }


def token_expires_at_epoch(expires_at: str) -> int:
    return int(_parse_datetime(expires_at).timestamp())


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_expired(expires_at: str) -> bool:
    return _parse_datetime(expires_at) <= datetime.now(ZoneInfo("Asia/Shanghai"))


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed
