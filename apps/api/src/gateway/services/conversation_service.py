from __future__ import annotations

import json
import sqlite3
from typing import Any

from .event_service import EventService
from .ids import new_id, now_iso, short_id
from .workspace_service import WorkspaceService


class ConversationService:
    def __init__(self, conn: sqlite3.Connection, workspace_service: WorkspaceService, event_service: EventService) -> None:
        self.conn = conn
        self.workspace_service = workspace_service
        self.event_service = event_service

    def create(
        self,
        *,
        user_id: str,
        title: str,
        user_request: str = "",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_iso()
        conversation_id = conversation_id or new_id("conv")
        self.conn.execute(
            """
            INSERT INTO conversations
            (conversation_id, user_id, title, status, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                user_id,
                title,
                "created",
                now,
                now,
                json.dumps({"source": "web"}, ensure_ascii=False),
            ),
        )
        if user_request:
            message_id = short_id("msg")
            self.conn.execute(
                """
                INSERT INTO messages
                (message_id, conversation_id, role, content, created_at, attachments_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, conversation_id, "user", user_request, now, "[]"),
            )
        self.conn.commit()
        self.workspace_service.create(user_id, conversation_id)
        self.event_service.append(conversation_id, "conversation_created", "Conversation created.")
        self.event_service.append(conversation_id, "workspace_created", "Workspace created.")
        return self.get(conversation_id)

    def get(self, conversation_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        messages = self.conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, message_id",
            (conversation_id,),
        ).fetchall()
        return {
            "conversation_id": row["conversation_id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "status": row["status"],
            "workspace_id": row["workspace_id"],
            "active_version_id": row["active_version_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "messages": [
                {
                    "message_id": msg["message_id"],
                    "role": msg["role"],
                    "content": msg["content"],
                    "created_at": msg["created_at"],
                    "attachments": json.loads(msg["attachments_json"] or "[]"),
                    "run_id": msg["run_id"],
                }
                for msg in messages
            ],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def list(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT conversation_id FROM conversations ORDER BY created_at DESC").fetchall()
        return [self.get(row["conversation_id"]) for row in rows]

    def latest_for_user(self, user_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT conversation_id FROM conversations
            WHERE user_id = ? AND status != 'archived'
            ORDER BY created_at DESC, conversation_id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return self.get(row["conversation_id"]) if row else None

    def get_or_create_for_user(
        self,
        *,
        user_id: str,
        title: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        if conversation_id:
            try:
                return self.get(conversation_id)
            except KeyError:
                return self.create(
                    user_id=user_id,
                    title=title,
                    user_request="",
                    conversation_id=conversation_id,
                )
        existing = self.latest_for_user(user_id)
        if existing is not None:
            return existing
        return self.create(user_id=user_id, title=title, user_request="")

    def update_status(self, conversation_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE conversations SET status = ?, updated_at = ? WHERE conversation_id = ?",
            (status, now_iso(), conversation_id),
        )
        self.conn.commit()

    def latest_user_request(self, conversation_id: str) -> str:
        row = self.conn.execute(
            """
            SELECT content FROM messages
            WHERE conversation_id = ? AND role = 'user'
            ORDER BY created_at DESC, message_id DESC LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        return row["content"] if row else ""

    def add_user_message(self, conversation_id: str, content: str, attachment_ids: list[str] | None = None) -> dict:
        message_id = short_id("msg")
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO messages
            (message_id, conversation_id, role, content, created_at, attachments_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, conversation_id, "user", content, now, json.dumps(attachment_ids or [], ensure_ascii=False)),
        )
        self.conn.commit()
        return {
            "message_id": message_id,
            "role": "user",
            "content": content,
            "created_at": now,
            "attachments": attachment_ids or [],
        }

    def attach_files_to_latest_user_message(self, conversation_id: str, file_ids: list[str]) -> None:
        if not file_ids:
            return
        row = self.conn.execute(
            """
            SELECT message_id, attachments_json FROM messages
            WHERE conversation_id = ? AND role = 'user'
            ORDER BY created_at DESC, message_id DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            return
        current = json.loads(row["attachments_json"] or "[]")
        merged = list(dict.fromkeys([*current, *file_ids]))
        self.conn.execute(
            "UPDATE messages SET attachments_json = ? WHERE message_id = ?",
            (json.dumps(merged, ensure_ascii=False), row["message_id"]),
        )
        self.conn.commit()

    def add_assistant_message(self, conversation_id: str, content: str, run_id: str) -> None:
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO messages
            (message_id, conversation_id, role, content, created_at, run_id, attachments_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (short_id("msg"), conversation_id, "assistant", content, now, run_id, "[]"),
        )
        self.conn.commit()
