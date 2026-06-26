from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .ids import now_iso


class WorkspaceService:
    def __init__(self, conn: sqlite3.Connection, data_dir: Path) -> None:
        self.conn = conn
        self.data_dir = data_dir

    def create(self, user_id: str, conversation_id: str) -> dict:
        workspace_id = f"ws_{conversation_id}"
        root = self.data_dir / "workspaces" / user_id / conversation_id
        for name in ("materials", "guidance", "outputs", "logs", "versions", ".gateway"):
            (root / name).mkdir(parents=True, exist_ok=True)
        policy = {
            "codex_cd": str(root),
            "sandbox": "workspace-write",
            "approval": "not_supported_by_current_cli",
            "network_enabled": False,
        }
        created_at = now_iso()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO workspaces
            (workspace_id, conversation_id, user_id, root_path, status, created_at, updated_at, policy_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                conversation_id,
                user_id,
                str(root),
                "active",
                created_at,
                created_at,
                json.dumps(policy, ensure_ascii=False),
            ),
        )
        self.conn.execute(
            "UPDATE conversations SET workspace_id = ?, updated_at = ? WHERE conversation_id = ?",
            (workspace_id, created_at, conversation_id),
        )
        self.conn.commit()
        return {
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "root_path": str(root),
            "status": "active",
            "created_at": created_at,
            "updated_at": created_at,
            "policy": policy,
        }

    def get_by_conversation(self, conversation_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM workspaces WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        return {
            "workspace_id": row["workspace_id"],
            "conversation_id": row["conversation_id"],
            "user_id": row["user_id"],
            "root_path": row["root_path"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "policy": json.loads(row["policy_json"] or "{}"),
        }
