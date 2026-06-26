from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import secrets
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from .ids import now_iso, short_id


class RunAuthService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_token(self, *, run_id: str, purpose: str = "asset_mcp", ttl_hours: int = 24) -> dict[str, Any]:
        token = f"{run_id}_{secrets.token_urlsafe(32)}"
        token_id = short_id("token")
        now = now_iso()
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
                self._hash(token),
                purpose,
                "active",
                expires_at,
                now,
                json.dumps({}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return {
            "token_id": token_id,
            "run_id": run_id,
            "token": token,
            "purpose": purpose,
            "expires_at": expires_at,
        }

    def authenticate(self, token: str, *, purpose: str = "asset_mcp") -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT * FROM run_auth_tokens
            WHERE token_hash = ?
              AND purpose = ?
              AND status = 'active'
            LIMIT 1
            """,
            (self._hash(token), purpose),
        ).fetchone()
        if row is None:
            raise PermissionError("invalid bearer token")
        if row["expires_at"] <= now_iso():
            raise PermissionError("bearer token expired")
        self.conn.execute(
            "UPDATE run_auth_tokens SET last_used_at = ? WHERE token_id = ?",
            (now_iso(), row["token_id"]),
        )
        self.conn.commit()
        return {
            "token_id": row["token_id"],
            "run_id": row["run_id"],
            "purpose": row["purpose"],
            "expires_at": row["expires_at"],
        }

    def _hash(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
