from __future__ import annotations

from datetime import datetime, timedelta
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .distribution_service import DistributionService
from .ids import now_iso, short_id
from .path_security import safe_join


class SessionWorkspaceService:
    def __init__(self, conn: sqlite3.Connection, data_dir: Path, distribution_service: DistributionService) -> None:
        self.conn = conn
        self.data_dir = data_dir
        self.distribution_service = distribution_service

    def create(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        device_id: str | None,
        ttl_hours: int = 24,
    ) -> dict[str, Any]:
        session_id = short_id("sess")
        root = self.data_dir / "sessions" / session_id
        for name in ("materials", "guidance", "outputs", "logs", "versions", ".gateway"):
            (root / name).mkdir(parents=True, exist_ok=True)
        now = now_iso()
        expires_at = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO run_sessions
            (session_id, run_id, conversation_id, user_id, device_id, root_path, status,
             expires_at, created_at, updated_at, manifest_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                run_id,
                conversation_id,
                user_id,
                device_id,
                str(root),
                "created",
                expires_at,
                now,
                now,
                json.dumps({"files": []}, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return self.get(session_id)

    def get(self, session_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM run_sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        return self._row_to_session(row)

    def get_by_run(self, run_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM run_sessions WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._row_to_session(row)

    def prepare_from_conversation(
        self,
        *,
        session_id: str,
        conversation_id: str,
        user_request: str,
        attachment_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        session = self.get(session_id)
        root = Path(session["root_path"])
        plan = self.distribution_service.build_plan(
            conversation_id=conversation_id,
            run_id=session["run_id"],
            user_request=user_request,
            attachment_ids=attachment_ids,
        )
        manifest_files = self.distribution_service.materialize_plan(session_root=root, run_id=session["run_id"], plan=plan)
        manifest = {
            "session_id": session_id,
            "run_id": session["run_id"],
            "conversation_id": conversation_id,
            "distribution": {
                "strategy_version": "v1",
                "default_mode": "original",
            },
            "files": manifest_files,
            "plan": plan,
        }
        self._update(session_id, "ready", manifest)
        return manifest

    def mark_status(self, session_id: str, status: str) -> None:
        self._update(session_id, status, None)

    def cleanup_expired(self, *, now: str | None = None) -> list[dict[str, Any]]:
        cutoff = now or now_iso()
        rows = self.conn.execute(
            """
            SELECT * FROM run_sessions
            WHERE expires_at <= ?
              AND status != 'cleaned'
            ORDER BY expires_at, session_id
            """,
            (cutoff,),
        ).fetchall()
        cleaned: list[dict[str, Any]] = []
        for row in rows:
            session = self._row_to_session(row)
            root = Path(session["root_path"])
            if root.exists():
                shutil.rmtree(root)
            self._update(session["session_id"], "cleaned", session["manifest"])
            cleaned.append(session)
        return cleaned

    def _update(self, session_id: str, status: str, manifest: dict[str, Any] | None) -> None:
        if manifest is None:
            self.conn.execute(
                "UPDATE run_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                (status, now_iso(), session_id),
            )
        else:
            self.conn.execute(
                "UPDATE run_sessions SET status = ?, updated_at = ?, manifest_json = ? WHERE session_id = ?",
                (status, now_iso(), json.dumps(manifest, ensure_ascii=False), session_id),
            )
        self.conn.commit()

    def _row_to_session(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "conversation_id": row["conversation_id"],
            "user_id": row["user_id"],
            "device_id": row["device_id"],
            "root_path": row["root_path"],
            "status": row["status"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "manifest": json.loads(row["manifest_json"] or "{}"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }
