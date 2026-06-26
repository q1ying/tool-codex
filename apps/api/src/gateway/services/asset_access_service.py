from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import sqlite3
from pathlib import Path
from typing import Any

from .asset_service import AssetService
from .event_service import EventService
from .ids import now_iso, short_id

MAX_CHUNK_SIZE = 1024 * 1024


class AssetAccessService:
    def __init__(self, conn: sqlite3.Connection, asset_service: AssetService) -> None:
        self.conn = conn
        self.asset_service = asset_service

    def get_run_context(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        session = self._session(run_id)
        metadata = json.loads(run["metadata_json"] or "{}")
        return {
            "run_id": run["run_id"],
            "conversation_id": run["conversation_id"],
            "workspace_id": run["workspace_id"],
            "user_id": run["user_id"],
            "status": run["status"],
            "started_at": run["started_at"],
            "ended_at": run["ended_at"],
            "session": session,
            "attachment_ids": list(metadata.get("attachment_ids") or []),
            "asset_mcp": {
                "enabled_tools": list((metadata.get("asset_mcp") or {}).get("enabled_tools") or []),
                "expires_at": (metadata.get("asset_mcp") or {}).get("expires_at"),
                "url": (metadata.get("asset_mcp") or {}).get("url"),
            },
            "candidate_asset_count": len(self.list_candidate_assets(run_id)),
        }

    def list_candidate_assets(self, run_id: str) -> list[dict[str, Any]]:
        self._run(run_id)
        rows = self.conn.execute(
            """
            SELECT run_assets.usage_type,
                   run_assets.local_path,
                   run_assets.reason,
                   run_assets.metadata_json AS run_metadata_json,
                   assets.*
            FROM run_assets
            JOIN assets ON assets.asset_id = run_assets.asset_id
            WHERE run_assets.run_id = ?
              AND run_assets.usage_type IN ('candidate', 'materialized', 'reference_only')
              AND assets.status NOT IN ('deleted', 'rejected')
            ORDER BY
              CASE run_assets.usage_type
                WHEN 'candidate' THEN 0
                WHEN 'materialized' THEN 1
                ELSE 2
              END,
              assets.created_at,
              assets.asset_id
            """,
            (run_id,),
        ).fetchall()
        by_asset: dict[str, dict[str, Any]] = {}
        for row in rows:
            asset_id = row["asset_id"]
            item = by_asset.setdefault(asset_id, self._asset_item(row))
            usage = row["usage_type"]
            if usage not in item["run_usage_types"]:
                item["run_usage_types"].append(usage)
            if row["local_path"] and not item.get("local_path"):
                item["local_path"] = row["local_path"]
            if row["reason"] and not item.get("why_included"):
                item["why_included"] = row["reason"]
        return list(by_asset.values())

    def list_conversation_assets(self, run_id: str) -> list[dict[str, Any]]:
        run = self._run(run_id)
        rows = self.conn.execute(
            """
            SELECT assets.* FROM assets
            WHERE assets.conversation_id = ?
              AND assets.status = 'ready'
            ORDER BY assets.created_at, assets.asset_id
            """,
            (run["conversation_id"],),
        ).fetchall()
        return [self._asset_item(row) for row in rows]

    def search_assets(self, run_id: str, query: str = "", *, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 20), 100))
        merged: dict[str, dict[str, Any]] = {}
        for item in [*self.list_candidate_assets(run_id), *self.list_conversation_assets(run_id)]:
            merged.setdefault(item["asset_id"], item)
        items = list(merged.values())
        terms = [term for term in re.split(r"\s+", query.lower().strip()) if term]
        if not terms:
            return items[:limit]
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            haystack = " ".join(
                str(item.get(key) or "")
                for key in ("filename", "original_filename", "summary", "kind", "role", "ext", "branch_key")
            ).lower()
            metadata_text = json.dumps(item.get("metadata") or {}, ensure_ascii=False).lower()
            score = sum(2 if term in haystack else 1 if term in metadata_text else 0 for term in terms)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("created_at") or "")))
        return [item for _, item in scored[:limit]]

    def get_asset_summary(self, run_id: str, asset_id: str) -> dict[str, Any]:
        asset = self._authorized_asset(run_id, asset_id)
        derivatives = self.conn.execute(
            """
            SELECT * FROM asset_derivatives
            WHERE asset_id = ?
              AND status = 'ready'
            ORDER BY created_at DESC, derivative_id DESC
            """,
            (asset_id,),
        ).fetchall()
        item = self._asset_dict_item(asset)
        item["available_derivatives"] = [
            {
                "derivative_id": row["derivative_id"],
                "derivative_type": row["derivative_type"],
                "mime_type": row["mime_type"],
                "size_bytes": row["size_bytes"],
                "sha256": row["sha256"],
                "created_at": row["created_at"],
            }
            for row in derivatives
        ]
        return item

    def read_asset_chunk(
        self,
        run_id: str,
        asset_id: str,
        *,
        chunk_index: int = 0,
        chunk_size: int = 8192,
    ) -> dict[str, Any]:
        if chunk_index < 0:
            raise ValueError("chunk_index must be >= 0")
        if chunk_size <= 0 or chunk_size > MAX_CHUNK_SIZE:
            raise ValueError(f"chunk_size must be between 1 and {MAX_CHUNK_SIZE}")
        asset = self._authorized_asset(run_id, asset_id)
        iterator = iter(self.asset_service.storage.iter_bytes(asset["object_key"], chunk_size))
        chunk = b""
        for index in range(chunk_index + 1):
            try:
                chunk = next(iterator)
            except StopIteration:
                chunk = b""
                break
            if index == chunk_index:
                break
        try:
            next(iterator)
            is_last = False
        except StopIteration:
            is_last = True
        text, is_text = _decode_chunk(chunk)
        payload = {
            "run_id": run_id,
            "asset_id": asset_id,
            "chunk_index": chunk_index,
            "chunk_size": chunk_size,
            "byte_start": chunk_index * chunk_size,
            "bytes_read": len(chunk),
            "is_last": is_last,
            "encoding": "utf-8",
            "text": text,
            "is_text": is_text,
        }
        if not is_text:
            payload["base64"] = base64.b64encode(chunk).decode("ascii")
        return payload

    def download_url(self, run_id: str, asset_id: str) -> dict[str, Any]:
        asset = self._authorized_asset(run_id, asset_id)
        self.asset_service.record_run_asset(
            run_id=run_id,
            asset_id=asset_id,
            usage_type="download_url",
            reason="download URL requested",
            metadata={"requested_at": now_iso()},
        )
        return {
            "run_id": run_id,
            "asset_id": asset_id,
            "download_url": self.asset_service.storage.presign_download(asset["object_key"]),
            "expires_in_seconds": self.asset_service.storage.settings.object_storage_presign_expires_seconds,
        }

    def upload_url(self, run_id: str, *, filename: str, content_type: str | None = None, role: str = "artifact") -> dict[str, Any]:
        run = self._run(run_id)
        safe_name = _sanitize_filename(filename)
        artifact_id = short_id("artifact")
        object_key = _artifact_object_key(
            owner_user_id=run["user_id"],
            conversation_id=run["conversation_id"],
            run_id=run_id,
            artifact_id=artifact_id,
            filename=safe_name,
        )
        return {
            "run_id": run_id,
            "artifact_id": artifact_id,
            "object_key": object_key,
            "upload_url": self.asset_service.storage.presign_upload(object_key, content_type),
            "method": "PUT",
            "content_type": content_type,
            "role": role,
            "expires_in_seconds": self.asset_service.storage.settings.object_storage_presign_expires_seconds,
        }

    def complete_artifact(
        self,
        run_id: str,
        *,
        object_key: str,
        filename: str,
        size_bytes: int | None = None,
        sha256: str | None = None,
        content_type: str | None = None,
        role: str = "artifact",
        parent_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        run = self._run(run_id)
        expected_prefix = _artifact_prefix(run["user_id"], run["conversation_id"], run_id)
        if not object_key.startswith(expected_prefix):
            raise PermissionError("object_key is outside this run's artifact upload scope")
        if not self.asset_service.storage.exists(object_key):
            raise ValueError("uploaded artifact object does not exist")
        actual_size = self.asset_service.storage.object_size(object_key)
        if size_bytes is not None and int(size_bytes) != actual_size:
            raise ValueError(f"artifact size mismatch: expected {size_bytes}, got {actual_size}")
        actual_sha256 = _object_sha256(self.asset_service.storage.iter_bytes(object_key))
        if sha256 and sha256 != actual_sha256:
            raise ValueError("artifact sha256 mismatch")
        authorized_parent_ids = []
        for parent_asset_id in parent_asset_ids or []:
            authorized_parent_ids.append(self._authorized_asset(run_id, parent_asset_id)["asset_id"])

        safe_name = _sanitize_filename(filename)
        file_id = short_id("file")
        stored_filename = f"{file_id}_{safe_name}"
        mime_type = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        metadata = {
            "description": "Codex MCP uploaded artifact",
            "branch_key": safe_name,
            "duplicate_upload_skipped": False,
            "is_generated_output": True,
            "candidate": True,
            "status": "candidate",
            "source_run_id": run_id,
            "generated_from_file_ids": authorized_parent_ids,
            "role": role,
        }
        asset = self.asset_service.create_from_object(
            asset_id=file_id,
            owner_user_id=run["user_id"],
            scope_type="run",
            conversation_id=run["conversation_id"],
            run_id=run_id,
            original_filename=safe_name,
            stored_filename=stored_filename,
            object_key=object_key,
            mime_type=mime_type,
            size_bytes=actual_size,
            sha256=actual_sha256,
            relation_type="generated_output",
            metadata=metadata,
        )
        relative_path = f"versions/{run_id}/{stored_filename}"
        self.conn.execute(
            """
            INSERT INTO file_assets
            (file_id, conversation_id, user_id, kind, original_filename, stored_filename, relative_path,
             mime_type, size_bytes, sha256, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                run["conversation_id"],
                run["user_id"],
                "output",
                safe_name,
                stored_filename,
                relative_path,
                mime_type,
                actual_size,
                actual_sha256,
                now_iso(),
                json.dumps({**metadata, "archive_relative_path": relative_path}, ensure_ascii=False),
            ),
        )
        for parent_asset_id in authorized_parent_ids:
            self.asset_service.record_lineage(
                parent_asset_id=parent_asset_id,
                child_asset_id=file_id,
                relation="generated_from",
                run_id=run_id,
                metadata={"via": "mcp_complete_artifact"},
            )
        self.asset_service.record_run_asset(
            run_id=run_id,
            asset_id=file_id,
            usage_type="output",
            reason="artifact completed through MCP",
            metadata={"relative_path": relative_path, "object_key": object_key},
        )
        self.conn.commit()
        return {"asset": asset, "file": {"file_id": file_id, "relative_path": relative_path}}

    def report_progress(
        self,
        run_id: str,
        *,
        message: str,
        progress: float | None = None,
        total: float | None = None,
        level: str = "info",
    ) -> dict[str, Any]:
        run = self._run(run_id)
        safe_level = level if level in {"info", "warning", "error"} else "info"
        EventService(self.conn).append(
            run["conversation_id"],
            "mcp_progress",
            message,
            run_id=run_id,
            level=safe_level,
            payload={"progress": progress, "total": total},
        )
        return {"ok": True, "run_id": run_id}

    def _run(self, run_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM codex_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    def _session(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM run_sessions WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "root_path": row["root_path"],
            "status": row["status"],
            "expires_at": row["expires_at"],
            "manifest": json.loads(row["manifest_json"] or "{}"),
        }

    def _authorized_asset(self, run_id: str, asset_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        asset = self.asset_service.get(asset_id)
        if asset["status"] in {"deleted", "rejected"}:
            raise PermissionError(f"asset {asset_id} is {asset['status']}")
        if asset.get("run_id") == run_id:
            return asset
        row = self.conn.execute(
            "SELECT 1 FROM run_assets WHERE run_id = ? AND asset_id = ? LIMIT 1",
            (run_id, asset_id),
        ).fetchone()
        if row is not None:
            return asset
        if asset.get("conversation_id") == run["conversation_id"] and asset["status"] == "ready":
            return asset
        raise PermissionError(f"asset {asset_id} is not authorized for run {run_id}")

    def _asset_item(self, row: sqlite3.Row) -> dict[str, Any]:
        return self._asset_dict_item(dict(row), row=row)

    def _asset_dict_item(self, asset: dict[str, Any], *, row: sqlite3.Row | None = None) -> dict[str, Any]:
        metadata = _metadata(asset)
        run_metadata = _metadata({"metadata": row["run_metadata_json"]} if row is not None and "run_metadata_json" in row.keys() else {})
        summary = str(metadata.get("description") or run_metadata.get("summary") or "").strip()
        if not summary:
            summary = _default_summary(asset)
        return {
            "asset_id": asset["asset_id"],
            "file_id": asset["asset_id"],
            "filename": asset["original_filename"],
            "original_filename": asset["original_filename"],
            "kind": asset.get("kind"),
            "role": asset.get("role"),
            "mime_type": asset.get("mime_type"),
            "ext": asset.get("ext") or Path(str(asset.get("stored_filename") or "")).suffix.lower(),
            "size_bytes": asset.get("size_bytes") or 0,
            "sha256": asset.get("sha256"),
            "status": asset.get("status"),
            "branch_id": asset.get("branch_id"),
            "branch_key": asset.get("branch_key"),
            "version_no": asset.get("version_no"),
            "created_at": asset.get("created_at"),
            "updated_at": asset.get("updated_at"),
            "summary": summary,
            "why_included": run_metadata.get("summary") or (row["reason"] if row is not None and "reason" in row.keys() else ""),
            "selected_mode": run_metadata.get("selected_mode") or (row["usage_type"] if row is not None and "usage_type" in row.keys() else "conversation_asset"),
            "target_path": row["local_path"] if row is not None and "local_path" in row.keys() else None,
            "local_path": row["local_path"] if row is not None and "local_path" in row.keys() else None,
            "run_usage_types": [],
            "metadata": metadata,
        }


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("metadata") or item.get("metadata_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _default_summary(asset: dict[str, Any]) -> str:
    return (
        f"{asset.get('original_filename') or asset.get('asset_id')}，"
        f"类型={asset.get('kind') or 'asset'}，"
        f"角色={asset.get('role') or 'unknown'}，"
        f"扩展名={asset.get('ext') or Path(str(asset.get('stored_filename') or '')).suffix.lower() or 'unknown'}，"
        f"大小={int(asset.get('size_bytes') or 0)} bytes。"
    )


def _decode_chunk(chunk: bytes) -> tuple[str, bool]:
    try:
        return chunk.decode("utf-8"), True
    except UnicodeDecodeError:
        return chunk.decode("utf-8", errors="replace"), False


def _sanitize_filename(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name).strip("._")
    if not name:
        raise ValueError("empty filename")
    return name


def _artifact_prefix(owner_user_id: str, conversation_id: str, run_id: str) -> str:
    return f"users/{owner_user_id}/conversations/{conversation_id}/runs/{run_id}/artifacts/"


def _artifact_object_key(*, owner_user_id: str, conversation_id: str, run_id: str, artifact_id: str, filename: str) -> str:
    return f"{_artifact_prefix(owner_user_id, conversation_id, run_id)}{artifact_id}/{filename}"


def _object_sha256(chunks: Any) -> str:
    hasher = hashlib.sha256()
    for chunk in chunks:
        hasher.update(chunk)
    return hasher.hexdigest()
