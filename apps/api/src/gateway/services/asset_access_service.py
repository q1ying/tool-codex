from __future__ import annotations

import json
import hashlib
import sqlite3
from typing import Any

from .asset_service import AssetService
from .ids import now_iso, short_id


class AssetAccessService:
    def __init__(self, conn: sqlite3.Connection, asset_service: AssetService) -> None:
        self.conn = conn
        self.asset_service = asset_service

    def run_context(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        session = self.conn.execute("SELECT * FROM run_sessions WHERE run_id = ?", (run_id,)).fetchone()
        metadata = json.loads(run["metadata_json"] or "{}")
        return {
            "run_id": run["run_id"],
            "conversation_id": run["conversation_id"],
            "user_id": run["user_id"],
            "status": run["status"],
            "session_id": session["session_id"] if session else metadata.get("session_id"),
            "asset_mcp": metadata.get("asset_mcp", {}),
        }

    def list_candidate_assets(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT assets.*, run_assets.usage_type, run_assets.reason, run_assets.local_path,
                   run_assets.metadata_json AS run_asset_metadata_json
            FROM run_assets
            JOIN assets ON assets.asset_id = run_assets.asset_id
            WHERE run_assets.run_id = ?
              AND run_assets.usage_type IN ('candidate', 'materialized', 'reference_only')
              AND assets.status = 'ready'
            ORDER BY assets.kind, assets.created_at, assets.asset_id
            """,
            (run_id,),
        ).fetchall()
        return [self._asset_row_to_public(row) for row in rows]

    def list_conversation_assets(self, run_id: str) -> list[dict[str, Any]]:
        run = self._run(run_id)
        rows = self.conn.execute(
            """
            SELECT DISTINCT assets.* FROM assets
            JOIN asset_links ON asset_links.asset_id = assets.asset_id
            WHERE asset_links.conversation_id = ?
              AND assets.owner_user_id = ?
              AND assets.status = 'ready'
            ORDER BY assets.kind, assets.created_at, assets.asset_id
            """,
            (run["conversation_id"], run["user_id"]),
        ).fetchall()
        return [self._asset_row_to_public(row) for row in rows]

    def search_assets(self, run_id: str, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        query = query.strip().lower()
        if not query:
            return self.list_candidate_assets(run_id)[:limit]
        tokens = [token for token in query.replace("_", " ").replace("-", " ").split() if token]
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in self.list_conversation_assets(run_id):
            haystack = " ".join(
                str(value or "")
                for value in (
                    item.get("filename"),
                    item.get("kind"),
                    item.get("role"),
                    item.get("ext"),
                    item.get("summary"),
                    json.dumps(item.get("metadata") or {}, ensure_ascii=False),
                )
            ).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], pair[1].get("version_no") or 0, pair[1].get("filename") or ""), reverse=True)
        return [item for _, item in scored[:limit]]

    def get_asset_summary(self, run_id: str, asset_id: str) -> dict[str, Any]:
        self._assert_allowed_asset(run_id, asset_id)
        asset = self.asset_service.get(asset_id)
        return self._asset_to_public(asset)

    def download_url(self, run_id: str, asset_id: str) -> dict[str, Any]:
        self._assert_allowed_asset(run_id, asset_id)
        asset = self.asset_service.get(asset_id)
        if asset["status"] != "ready":
            raise PermissionError("asset is not ready")
        url = self.asset_service.storage.presign_download(asset["object_key"])
        self.asset_service.record_run_asset(
            run_id=run_id,
            asset_id=asset_id,
            usage_type="download_url_issued",
            reason="temporary object storage download URL issued",
            metadata={"object_key": asset["object_key"]},
        )
        return {
            "asset_id": asset_id,
            "url": url,
            "method": "GET",
            "expires_in_seconds": self.asset_service.storage.settings.object_storage_presign_expires_seconds,
            "filename": asset["original_filename"],
            "sha256": asset["sha256"],
            "size_bytes": asset["size_bytes"],
        }

    def read_asset_chunk(self, run_id: str, asset_id: str, *, chunk_index: int = 0, chunk_size: int = 8192) -> dict[str, Any]:
        if chunk_index < 0:
            raise ValueError("chunk_index must be >= 0")
        if chunk_size < 1 or chunk_size > 64 * 1024:
            raise ValueError("chunk_size must be between 1 and 65536 bytes")
        self._assert_allowed_asset(run_id, asset_id)
        asset = self.asset_service.get(asset_id)
        start = chunk_index * chunk_size
        end = start + chunk_size
        offset = 0
        chunks: list[bytes] = []
        for chunk in self.asset_service.storage.iter_bytes(asset["object_key"], chunk_size=1024 * 1024):
            next_offset = offset + len(chunk)
            if next_offset <= start:
                offset = next_offset
                continue
            if offset >= end:
                break
            slice_start = max(0, start - offset)
            slice_end = min(len(chunk), end - offset)
            chunks.append(chunk[slice_start:slice_end])
            offset = next_offset
        data = b"".join(chunks)
        text = data.decode("utf-8", errors="replace")
        self.asset_service.record_run_asset(
            run_id=run_id,
            asset_id=asset_id,
            usage_type="chunk_read",
            reason="asset chunk read through MCP",
            metadata={"chunk_index": chunk_index, "chunk_size": chunk_size, "byte_start": start, "byte_end": start + len(data)},
        )
        return {
            "asset_id": asset_id,
            "filename": asset["original_filename"],
            "chunk_index": chunk_index,
            "chunk_size": chunk_size,
            "byte_start": start,
            "byte_end": start + len(data),
            "is_last": start + len(data) >= int(asset.get("size_bytes") or 0),
            "encoding": "utf-8",
            "text": text,
        }

    def upload_url(self, run_id: str, *, filename: str, content_type: str | None = None, role: str = "artifact") -> dict[str, Any]:
        run = self._run(run_id)
        safe_name = filename.replace("\\", "/").split("/")[-1] or "artifact.bin"
        object_key = f"users/{run['user_id']}/conversations/{run['conversation_id']}/runs/{run_id}/pending-artifacts/{safe_name}"
        url = self.asset_service.storage.presign_upload(object_key, content_type)
        return {
            "run_id": run_id,
            "object_key": object_key,
            "url": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type} if content_type else {},
            "expires_in_seconds": self.asset_service.storage.settings.object_storage_presign_expires_seconds,
            "role": role,
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
        safe_name = filename.replace("\\", "/").split("/")[-1] or "artifact.bin"
        allowed_prefix = f"users/{run['user_id']}/conversations/{run['conversation_id']}/runs/{run_id}/pending-artifacts/"
        if not object_key.startswith(allowed_prefix):
            raise PermissionError("artifact object_key is outside this run upload scope")
        for parent_id in parent_asset_ids or []:
            self._assert_allowed_asset(run_id, parent_id)
        actual_size = self.asset_service.storage.object_size(object_key)
        if size_bytes is not None and int(size_bytes) != actual_size:
            raise ValueError(f"artifact size mismatch: expected {size_bytes}, got {actual_size}")
        actual_sha256 = self._object_sha256(object_key)
        if sha256 and sha256 != actual_sha256:
            raise ValueError("artifact sha256 mismatch")
        asset_id = short_id("file")
        stored_filename = f"{asset_id}_{safe_name}"
        metadata = {
            "description": "Codex direct-uploaded output",
            "branch_key": safe_name,
            "is_generated_output": True,
            "candidate": True,
            "status": "candidate",
            "source_run_id": run_id,
            "direct_upload": True,
            "role": role,
            "generated_from_file_ids": parent_asset_ids or [],
            "object_key": object_key,
        }
        asset = self.asset_service.create_from_object(
            asset_id=asset_id,
            owner_user_id=run["user_id"],
            scope_type="run",
            conversation_id=run["conversation_id"],
            run_id=run_id,
            original_filename=safe_name,
            stored_filename=stored_filename,
            object_key=object_key,
            mime_type=content_type,
            size_bytes=actual_size,
            sha256=actual_sha256,
            relation_type="generated_output",
            metadata=metadata,
        )
        now = now_iso()
        relative_path = f"versions/{run_id}/{stored_filename}"
        self.conn.execute(
            """
            INSERT INTO file_assets
            (file_id, conversation_id, user_id, kind, original_filename, stored_filename, relative_path,
             mime_type, size_bytes, sha256, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                run["conversation_id"],
                run["user_id"],
                "output",
                safe_name,
                stored_filename,
                relative_path,
                content_type,
                actual_size,
                actual_sha256,
                now,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        for parent_id in parent_asset_ids or []:
            self.asset_service.record_lineage(
                parent_asset_id=parent_id,
                child_asset_id=asset_id,
                relation="generated_from",
                run_id=run_id,
                metadata={"direct_upload": True},
            )
        self.asset_service.record_run_asset(
            run_id=run_id,
            asset_id=asset_id,
            usage_type="output",
            local_path=None,
            reason="direct-uploaded artifact completed",
            metadata={"object_key": object_key, "role": role, "relative_path": relative_path},
        )
        self.conn.execute(
            """
            INSERT INTO events
            (event_id, conversation_id, run_id, type, level, message, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                short_id("evt"),
                run["conversation_id"],
                run_id,
                "artifact_completed",
                "info",
                f"Registered direct-uploaded artifact {safe_name}.",
                now_iso(),
                json.dumps({"asset_id": asset_id, "object_key": object_key, "role": role}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return {"asset": asset, "relative_path": relative_path}

    def report_progress(self, run_id: str, *, status: str, message: str = "") -> dict[str, Any]:
        run = self._run(run_id)
        self.conn.execute(
            """
            INSERT INTO events
            (event_id, conversation_id, run_id, type, level, message, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                short_id("evt"),
                run["conversation_id"],
                run_id,
                "asset_mcp_progress",
                "info",
                message or status,
                now_iso(),
                json.dumps({"status": status}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return {"ok": True, "run_id": run_id, "status": status}

    def _run(self, run_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM codex_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    def _object_sha256(self, object_key: str) -> str:
        hasher = hashlib.sha256()
        for chunk in self.asset_service.storage.iter_bytes(object_key):
            hasher.update(chunk)
        return hasher.hexdigest()

    def _assert_allowed_asset(self, run_id: str, asset_id: str) -> None:
        run = self._run(run_id)
        row = self.conn.execute(
            """
            SELECT 1 FROM run_assets
            WHERE run_id = ?
              AND asset_id = ?
              AND usage_type IN ('candidate', 'materialized', 'reference_only')
            LIMIT 1
            """,
            (run_id, asset_id),
        ).fetchone()
        if row is not None:
            return
        row = self.conn.execute(
            """
            SELECT 1 FROM assets
            WHERE asset_id = ?
              AND owner_user_id = ?
              AND conversation_id = ?
              AND status = 'ready'
            LIMIT 1
            """,
            (asset_id, run["user_id"], run["conversation_id"]),
        ).fetchone()
        if row is None:
            raise PermissionError("asset is outside this run scope")

    def _asset_row_to_public(self, row: sqlite3.Row) -> dict[str, Any]:
        asset = self.asset_service._row_to_asset(row)
        public = self._asset_to_public(asset)
        if "usage_type" in row.keys():
            public["usage_type"] = row["usage_type"]
            public["why_included"] = row["reason"]
            public["local_path"] = row["local_path"]
            metadata = json.loads(row["run_asset_metadata_json"] or "{}")
            public["summary"] = metadata.get("summary") or public["summary"]
            public["selected_mode"] = metadata.get("selected_mode")
        return public

    def _asset_to_public(self, asset: dict[str, Any]) -> dict[str, Any]:
        metadata = asset.get("metadata") or {}
        description = str(metadata.get("description") or "").strip()
        summary = description or (
            f"{asset['original_filename']}，kind={asset.get('kind')}，role={asset.get('role')}，"
            f"ext={asset.get('ext')}，size={asset.get('size_bytes')} bytes。"
        )
        return {
            "asset_id": asset["asset_id"],
            "filename": asset["original_filename"],
            "kind": asset.get("kind"),
            "role": asset.get("role"),
            "mime_type": asset.get("mime_type"),
            "ext": asset.get("ext"),
            "size_bytes": asset.get("size_bytes"),
            "sha256": asset.get("sha256"),
            "branch_id": asset.get("branch_id"),
            "branch_key": asset.get("branch_key"),
            "version_no": asset.get("version_no"),
            "summary": summary,
            "metadata": metadata,
        }
