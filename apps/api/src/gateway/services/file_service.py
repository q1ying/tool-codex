from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import BinaryIO

from .asset_service import AssetService
from .event_service import EventService
from .ids import now_iso, short_id
from .path_security import safe_join
from .workspace_service import WorkspaceService

ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".xlsx", ".xls", ".json", ".docx", ".pdf"}
IGNORED_OUTPUT_FILENAMES = {"final.md", "result.json"}


class FileService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        workspace_service: WorkspaceService,
        event_service: EventService,
        asset_service: AssetService | None = None,
    ) -> None:
        self.conn = conn
        self.workspace_service = workspace_service
        self.event_service = event_service
        self.asset_service = asset_service

    def save_upload(
        self,
        *,
        conversation_id: str,
        user_id: str,
        kind: str,
        original_filename: str,
        content_type: str | None,
        fileobj: BinaryIO,
        description: str = "",
    ) -> dict:
        if kind not in {"material", "guidance"}:
            raise ValueError("kind must be material or guidance")
        safe_name = self._sanitize_filename(original_filename)
        ext = Path(safe_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"unsupported file type: {ext}")

        workspace = self.workspace_service.get_by_conversation(conversation_id)
        workspace_root = Path(workspace["root_path"])
        folder = {"material": "materials", "guidance": "guidance"}[kind]
        temp_dir = safe_join(workspace_root, ".gateway/upload_tmp")
        temp_dir.mkdir(parents=True, exist_ok=True)

        hasher = hashlib.sha256()
        size = 0
        tmp_name = ""
        with tempfile.NamedTemporaryFile(delete=False, dir=temp_dir) as out:
            tmp_name = out.name
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                hasher.update(chunk)
                out.write(chunk)
        sha256 = hasher.hexdigest()

        duplicate = self._find_duplicate(conversation_id, kind, sha256)
        if duplicate is not None:
            Path(tmp_name).unlink(missing_ok=True)
            duplicate["metadata"]["duplicate_upload_skipped"] = True
            duplicate["metadata"]["duplicate_original_filename"] = original_filename
            self.event_service.append(
                conversation_id,
                "file_duplicate_skipped",
                f"Skipped duplicate upload {original_filename}.",
                payload={
                    "file_id": duplicate["file_id"],
                    "sha256": sha256,
                    "relative_path": duplicate["relative_path"],
                },
            )
            return duplicate

        replaced = self._same_name_uploads(conversation_id, kind, original_filename)
        file_id = short_id("file")
        stored_filename = f"{file_id}_{safe_name}"
        relative_path = f"{folder}/{stored_filename}"
        target = safe_join(workspace_root, relative_path)
        tmp_path = Path(tmp_name)

        created_at = now_iso()
        metadata = {
            "description": description,
            "branch_key": original_filename,
            "branch_index": 1,
            "duplicate_upload_skipped": False,
            "replaced_file_ids": [item["file_id"] for item in replaced],
        }
        if self.asset_service is not None:
            try:
                self.asset_service.create_from_path(
                    asset_id=file_id,
                    owner_user_id=user_id,
                    scope_type="conversation",
                    conversation_id=conversation_id,
                    original_filename=original_filename,
                    stored_filename=stored_filename,
                    source_path=tmp_path,
                    mime_type=content_type,
                    size_bytes=size,
                    sha256=sha256,
                    relation_type={"guidance": "guidance", "material": "chat_attachment"}[kind],
                    metadata={**metadata, "workspace_kind": kind, "workspace_relative_path": relative_path},
                )
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(tmp_name, target)
        asset = {
            "file_id": file_id,
            "asset_id": file_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "kind": kind,
            "original_filename": original_filename,
            "stored_filename": stored_filename,
            "relative_path": relative_path,
            "mime_type": content_type,
            "size_bytes": size,
            "sha256": sha256,
            "created_at": created_at,
            "metadata": metadata,
        }
        self.conn.execute(
            """
            INSERT INTO file_assets
            (file_id, conversation_id, user_id, kind, original_filename, stored_filename, relative_path,
             mime_type, size_bytes, sha256, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                conversation_id,
                user_id,
                kind,
                original_filename,
                stored_filename,
                relative_path,
                content_type,
                size,
                sha256,
                created_at,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        self._delete_replaced_uploads(replaced)
        self.conn.commit()
        self.event_service.append(
            conversation_id,
            "file_uploaded",
            f"Uploaded {original_filename}.",
            payload={
                "file_id": file_id,
                "relative_path": relative_path,
                "replaced_file_ids": [item["file_id"] for item in replaced],
            },
        )
        return asset

    def list(self, conversation_id: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT file_assets.*, assets.status AS asset_status, assets.branch_id, assets.branch_key,
                   assets.version_no, assets.accepted_at, assets.deleted_at
            FROM file_assets
            LEFT JOIN assets ON assets.asset_id = file_assets.file_id
            WHERE file_assets.conversation_id = ?
            ORDER BY file_assets.created_at, file_assets.file_id
            """,
            (conversation_id,),
        ).fetchall()
        return [
            {
                "file_id": row["file_id"],
                "kind": row["kind"],
                "original_filename": row["original_filename"],
                "relative_path": row["relative_path"],
                "size_bytes": row["size_bytes"],
                "mime_type": row["mime_type"],
                "sha256": row["sha256"],
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
                "asset_status": row["asset_status"] or "legacy",
                "branch_id": row["branch_id"],
                "branch_key": row["branch_key"],
                "version_no": row["version_no"],
                "accepted_at": row["accepted_at"],
                "deleted_at": row["deleted_at"],
            }
            for row in rows
        ]

    def get(self, file_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM file_assets WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        if row is None:
            raise KeyError(file_id)
        return {
            "file_id": row["file_id"],
            "conversation_id": row["conversation_id"],
            "user_id": row["user_id"],
            "kind": row["kind"],
            "original_filename": row["original_filename"],
            "stored_filename": row["stored_filename"],
            "relative_path": row["relative_path"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def resolve_asset_path(self, file_id: str) -> Path:
        asset = self.get(file_id)
        workspace = self.workspace_service.get_by_conversation(asset["conversation_id"])
        return safe_join(Path(workspace["root_path"]), asset["relative_path"])

    def branch_summary(self, conversation_id: str) -> list[dict]:
        items = self.list(conversation_id)
        groups: dict[str, dict] = {}
        for item in items:
            metadata = item["metadata"]
            key = metadata.get("branch_key") or item["original_filename"]
            group = groups.setdefault(
                key,
                {
                    "branch_key": key,
                    "original_filename": key,
                    "stored_count": 0,
                    "branches": [],
                },
            )
            group["stored_count"] += 1
            group["branches"].append(
                {
                    "file_id": item["file_id"],
                    "kind": item["kind"],
                    "relative_path": item["relative_path"],
                    "sha256": item["sha256"],
                    "size_bytes": item["size_bytes"],
                    "created_at": item["created_at"],
                    "branch_index": metadata.get("branch_index", group["stored_count"]),
                    "description": metadata.get("description", ""),
                    "generated_from_file_ids": metadata.get("generated_from_file_ids", []),
                    "source_run_id": metadata.get("source_run_id"),
                    "is_generated_output": metadata.get("is_generated_output", False),
                    "asset_status": item.get("asset_status"),
                    "branch_id": item.get("branch_id"),
                    "version_no": item.get("version_no"),
                    "actual_filename": item["original_filename"],
                }
            )
        return list(groups.values())

    def register_run_outputs(
        self,
        *,
        conversation_id: str,
        user_id: str,
        run_id: str,
        source_file_ids: list[str],
        source_workspace_root: Path | None = None,
    ) -> list[dict]:
        workspace = self.workspace_service.get_by_conversation(conversation_id)
        workspace_root = Path(workspace["root_path"])
        scan_root = source_workspace_root or workspace_root
        effective_source_file_ids = self._effective_source_file_ids(run_id, source_file_ids)
        registered: list[dict] = []
        seen_sources: set[Path] = set()
        for folder in ("outputs", "output"):
            source_root = safe_join(scan_root, folder)
            if not source_root.exists():
                continue
            for path in source_root.rglob("*"):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved in seen_sources:
                    continue
                seen_sources.add(resolved)
                registered_asset = self._register_output_path(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    run_id=run_id,
                    source_file_ids=effective_source_file_ids,
                    source_path=path,
                    archive_workspace_root=workspace_root,
                    source_workspace_root=scan_root,
                )
                if registered_asset is not None:
                    registered.append(registered_asset)
        if registered:
            self.event_service.append(
                conversation_id,
                "run_outputs_registered",
                f"Registered {len(registered)} generated output file(s).",
                run_id=run_id,
                payload={"file_ids": [item["file_id"] for item in registered]},
            )
        return registered

    def copy_outputs_to_run_dir(self, workspace_root: Path, run_dir: Path) -> None:
        source = safe_join(workspace_root, "outputs")
        target = run_dir / "outputs"
        if target.exists():
            shutil.rmtree(target)
        if source.exists():
            shutil.copytree(source, target)

    def _sanitize_filename(self, filename: str) -> str:
        name = Path(filename).name
        name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name).strip("._")
        if not name:
            raise ValueError("empty filename")
        return name

    def _find_duplicate(self, conversation_id: str, kind: str, sha256: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT * FROM file_assets
            WHERE conversation_id = ? AND kind = ? AND sha256 = ?
            ORDER BY created_at, file_id
            LIMIT 1
            """,
            (conversation_id, kind, sha256),
        ).fetchone()
        if row is None:
            return None
        return {
            "file_id": row["file_id"],
            "conversation_id": row["conversation_id"],
            "user_id": row["user_id"],
            "kind": row["kind"],
            "original_filename": row["original_filename"],
            "stored_filename": row["stored_filename"],
            "relative_path": row["relative_path"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def _same_name_uploads(self, conversation_id: str, kind: str, original_filename: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT file_assets.*, assets.object_key, assets.status AS asset_status
            FROM file_assets
            LEFT JOIN assets ON assets.asset_id = file_assets.file_id
            WHERE file_assets.conversation_id = ?
              AND file_assets.kind = ?
              AND file_assets.original_filename = ?
            ORDER BY file_assets.created_at, file_assets.file_id
            """,
            (conversation_id, kind, original_filename),
        ).fetchall()
        return [
            {
                "file_id": row["file_id"],
                "relative_path": row["relative_path"],
                "object_key": row["object_key"],
                "asset_status": row["asset_status"],
            }
            for row in rows
        ]

    def _delete_replaced_uploads(self, items: list[dict]) -> None:
        if not items:
            return
        now = now_iso()
        for item in items:
            file_id = item["file_id"]
            if self.asset_service is not None and item.get("object_key") and item.get("asset_status") != "deleted":
                self.asset_service.storage.delete_object(item["object_key"])
            self.conn.execute(
                """
                UPDATE assets
                SET status = 'deleted',
                    deleted_at = COALESCE(deleted_at, ?),
                    updated_at = ?
                WHERE asset_id = ?
                """,
                (now, now, file_id),
            )
            self.conn.execute("DELETE FROM asset_links WHERE asset_id = ?", (file_id,))
            self.conn.execute("DELETE FROM run_assets WHERE asset_id = ?", (file_id,))
            self.conn.execute("DELETE FROM file_assets WHERE file_id = ?", (file_id,))

    def _next_branch_index(self, conversation_id: str, original_filename: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count FROM file_assets
            WHERE conversation_id = ? AND original_filename = ?
            """,
            (conversation_id, original_filename),
        ).fetchone()
        return int(row["count"] or 0) + 1

    def _exists(self, file_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM file_assets WHERE file_id = ?", (file_id,)).fetchone()
        return row is not None

    def _register_output_path(
        self,
        *,
        conversation_id: str,
        user_id: str,
        run_id: str,
        source_file_ids: list[str],
        source_path: Path,
        archive_workspace_root: Path,
        source_workspace_root: Path,
    ) -> dict | None:
        safe_name = self._sanitize_filename(source_path.name)
        if self._should_ignore_output(safe_name):
            return None
        parent_asset, is_revision = self._select_parent_asset_for_output(source_path, source_file_ids)
        if parent_asset is None:
            return None
        hasher = hashlib.sha256()
        size = 0
        with source_path.open("rb") as file:
            while True:
                chunk = file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                hasher.update(chunk)
        sha256 = hasher.hexdigest()
        latest_asset = self.asset_service.latest_for_branch(parent_asset["branch_id"]) if self.asset_service is not None and is_revision else None
        if latest_asset is not None and latest_asset.get("sha256") == sha256:
            return None
        duplicate = self.conn.execute(
            """
            SELECT * FROM file_assets
            WHERE conversation_id = ? AND kind = 'output' AND sha256 = ? AND metadata_json LIKE ?
            LIMIT 1
            """,
            (conversation_id, sha256, f'%"source_run_id": "{run_id}"%'),
        ).fetchone()
        if duplicate is not None:
            return None

        file_id = short_id("file")
        stored_filename = f"{file_id}_{safe_name}"
        relative_path = f"versions/{run_id}/{stored_filename}"
        target = safe_join(archive_workspace_root, relative_path)
        created_at = now_iso()
        branch_key = (parent_asset.get("branch_key") or parent_asset["original_filename"]) if is_revision else safe_name
        generated_from_file_ids = [parent_asset["asset_id"]] if parent_asset else []
        metadata = {
            "description": "Codex generated output",
            "branch_key": branch_key,
            "branch_index": self._next_branch_index_by_key(conversation_id, branch_key),
            "duplicate_upload_skipped": False,
            "is_generated_output": True,
            "candidate": True,
            "status": "candidate",
            "source_run_id": run_id,
            "generated_from_file_ids": generated_from_file_ids,
            "workspace_source_path": source_path.relative_to(source_workspace_root).as_posix(),
        }
        mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        if self.asset_service is not None:
            self.asset_service.create_from_path(
                asset_id=file_id,
                owner_user_id=user_id,
                scope_type="run",
                conversation_id=conversation_id,
                run_id=run_id,
                original_filename=safe_name,
                stored_filename=stored_filename,
                source_path=source_path,
                mime_type=mime_type,
                size_bytes=size,
                sha256=sha256,
                relation_type="generated_output",
                metadata={
                    **metadata,
                    "archive_relative_path": relative_path,
                    **({"asset_type": parent_asset.get("asset_type"), "role": parent_asset.get("role")} if is_revision else {}),
                },
            )
            self.asset_service.record_lineage(
                parent_asset_id=parent_asset["asset_id"],
                child_asset_id=file_id,
                relation="candidate_revision_of" if is_revision else "generated_from",
                run_id=run_id,
                metadata={"workspace_source_path": metadata["workspace_source_path"]},
            )
            self.asset_service.record_run_asset(
                run_id=run_id,
                asset_id=file_id,
                usage_type="output",
                local_path=metadata["workspace_source_path"],
                reason="generated output registered",
                metadata={"branch_key": branch_key, "relative_path": relative_path},
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)
        asset = {
            "file_id": file_id,
            "asset_id": file_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "kind": "output",
            "original_filename": safe_name,
            "stored_filename": stored_filename,
            "relative_path": relative_path,
            "mime_type": mime_type,
            "size_bytes": size,
            "sha256": sha256,
            "created_at": created_at,
            "metadata": metadata,
        }
        self.conn.execute(
            """
            INSERT INTO file_assets
            (file_id, conversation_id, user_id, kind, original_filename, stored_filename, relative_path,
             mime_type, size_bytes, sha256, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                conversation_id,
                user_id,
                "output",
                safe_name,
                stored_filename,
                relative_path,
                mime_type,
                size,
                sha256,
                created_at,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return asset

    def _effective_source_file_ids(self, run_id: str, source_file_ids: list[str]) -> list[str]:
        ordered: list[str] = []
        for file_id in source_file_ids:
            if file_id not in ordered:
                ordered.append(file_id)
        rows = self.conn.execute(
            """
            SELECT asset_id FROM run_assets
            WHERE run_id = ? AND usage_type = 'materialized'
            ORDER BY created_at, asset_id
            """,
            (run_id,),
        ).fetchall()
        for row in rows:
            asset_id = row["asset_id"]
            if asset_id not in ordered:
                ordered.append(asset_id)
        return ordered

    def _should_ignore_output(self, filename: str) -> bool:
        lower = filename.lower()
        if lower in IGNORED_OUTPUT_FILENAMES:
            return True
        if lower.endswith(".log"):
            return True
        return False

    def _select_parent_asset_for_output(self, output_path: Path, source_file_ids: list[str]) -> tuple[dict | None, bool]:
        if self.asset_service is None:
            return None, False
        output_ext = output_path.suffix.lower()
        candidates = []
        for file_id in source_file_ids:
            try:
                asset = self.asset_service.get(file_id)
            except KeyError:
                continue
            if asset["status"] != "ready" or not asset.get("branch_id") or asset.get("kind") == "guidance":
                continue
            candidates.append(asset)
        same_ext = [asset for asset in candidates if (asset.get("ext") or "").lower() == output_ext]
        if same_ext:
            return sorted(same_ext, key=lambda item: item.get("version_no") or 0, reverse=True)[0], True
        if output_ext in {".xlsx", ".xls", ".csv"}:
            sheets = [asset for asset in candidates if (asset.get("ext") or "").lower() in {".xlsx", ".xls", ".csv"}]
            if sheets:
                return sorted(sheets, key=lambda item: item.get("version_no") or 0, reverse=True)[0], True
        if candidates:
            return sorted(candidates, key=lambda item: item.get("version_no") or 0, reverse=True)[0], False
        return None, False

    def _next_branch_index_by_key(self, conversation_id: str, branch_key: str) -> int:
        rows = self.conn.execute(
            """
            SELECT metadata_json, original_filename FROM file_assets
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchall()
        count = 0
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            if (metadata.get("branch_key") or row["original_filename"]) == branch_key:
                count += 1
        return count + 1
