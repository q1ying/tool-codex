from __future__ import annotations

import json
import sqlite3
from typing import Any

from .ids import now_iso, short_id


class EventService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append(
        self,
        conversation_id: str,
        event_type: str,
        message: str,
        *,
        run_id: str | None = None,
        level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": short_id("evt"),
            "conversation_id": conversation_id,
            "run_id": run_id,
            "type": event_type,
            "level": level,
            "message": message,
            "created_at": now_iso(),
            "payload": payload or {},
        }
        self.conn.execute(
            """
            INSERT INTO events
            (event_id, conversation_id, run_id, type, level, message, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                conversation_id,
                run_id,
                event_type,
                level,
                message,
                event["created_at"],
                json.dumps(event["payload"], ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return event

    def list(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE conversation_id = ? ORDER BY created_at, event_id",
            (conversation_id,),
        ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "conversation_id": row["conversation_id"],
                "run_id": row["run_id"],
                "type": row["type"],
                "level": row["level"],
                "message": row["message"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload_json"] or "{}"),
            }
            for row in rows
        ]

