from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .ids import now_iso, short_id
from .storage_service import StorageService


class AssetService:
    def __init__(self, conn: sqlite3.Connection, storage: StorageService) -> None:
        self.conn = conn
        self.storage = storage

    def create_from_path(
        self,
        *,
        owner_user_id: str,
        scope_type: str,
        original_filename: str,
        stored_filename: str,
        source_path: Path,
        mime_type: str | None,
        size_bytes: int,
        sha256: str,
        project_id: str | None = None,
        conversation_id: str | None = None,
        source_message_id: str | None = None,
        relation_type: str = "chat_attachment",
        message_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        asset_id: str | None = None,
    ) -> dict[str, Any]:
        if scope_type not in {"project", "conversation", "run", "system"}:
            raise ValueError("scope_type must be project, conversation, run, or system.")
        asset_id = asset_id or short_id("asset")
        ext = Path(stored_filename).suffix.lower()
        asset_type = self._asset_type(ext, metadata or {}, relation_type)
        branch_key = self._normalize_branch_key(str((metadata or {}).get("branch_key") or original_filename))
        branch = self._ensure_branch(
            owner_user_id=owner_user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            branch_key=branch_key,
            asset_type=asset_type,
        )
        version_no = self._next_version_no(branch["branch_id"])
        role = str((metadata or {}).get("role") or self._role_for(relation_type, ext))
        kind = str((metadata or {}).get("kind") or self._kind_for(relation_type))
        object_key = self._object_key(
            owner_user_id=owner_user_id,
            scope_type=scope_type,
            project_id=project_id,
            conversation_id=conversation_id,
            run_id=run_id,
            asset_id=asset_id,
            filename=stored_filename,
        )
        self.storage.put_path(source_path, object_key, mime_type)
        now = now_iso()
        status = str((metadata or {}).get("status") or ("candidate" if relation_type == "generated_output" else "ready"))
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        self.conn.execute(
            """
            INSERT INTO assets
            (asset_id, owner_user_id, scope_type, project_id, conversation_id, run_id, source_message_id,
             original_filename, stored_filename, mime_type, ext, size_bytes, sha256,
             storage_backend, bucket, object_key, status, created_at, updated_at, metadata_json,
             branch_id, branch_key, version_no, role, kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                owner_user_id,
                scope_type,
                project_id,
                conversation_id,
                run_id,
                source_message_id,
                original_filename,
                stored_filename,
                mime_type,
                ext,
                size_bytes,
                sha256,
                self.storage.backend_name,
                self.storage.bucket,
                object_key,
                status,
                now,
                now,
                metadata_json,
                branch["branch_id"],
                branch_key,
                version_no,
                role,
                kind,
            ),
        )
        if status == "ready":
            self._update_branch_latest(branch["branch_id"], asset_id, now)
        self.link(
            asset_id=asset_id,
            relation_type=relation_type,
            project_id=project_id,
            conversation_id=conversation_id,
            message_id=message_id,
            run_id=run_id,
            metadata=metadata or {},
            commit=False,
        )
        self.conn.commit()
        return self.get(asset_id)

    def create_from_object(
        self,
        *,
        owner_user_id: str,
        scope_type: str,
        original_filename: str,
        stored_filename: str,
        object_key: str,
        mime_type: str | None,
        size_bytes: int,
        sha256: str,
        project_id: str | None = None,
        conversation_id: str | None = None,
        source_message_id: str | None = None,
        relation_type: str = "generated_output",
        message_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        asset_id: str | None = None,
    ) -> dict[str, Any]:
        if scope_type not in {"project", "conversation", "run", "system"}:
            raise ValueError("scope_type must be project, conversation, run, or system.")
        asset_id = asset_id or short_id("asset")
        ext = Path(stored_filename).suffix.lower()
        asset_type = self._asset_type(ext, metadata or {}, relation_type)
        branch_key = self._normalize_branch_key(str((metadata or {}).get("branch_key") or original_filename))
        branch = self._ensure_branch(
            owner_user_id=owner_user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            branch_key=branch_key,
            asset_type=asset_type,
        )
        version_no = self._next_version_no(branch["branch_id"])
        role = str((metadata or {}).get("role") or self._role_for(relation_type, ext))
        kind = str((metadata or {}).get("kind") or self._kind_for(relation_type))
        now = now_iso()
        status = str((metadata or {}).get("status") or ("candidate" if relation_type == "generated_output" else "ready"))
        self.conn.execute(
            """
            INSERT INTO assets
            (asset_id, owner_user_id, scope_type, project_id, conversation_id, run_id, source_message_id,
             original_filename, stored_filename, mime_type, ext, size_bytes, sha256,
             storage_backend, bucket, object_key, status, created_at, updated_at, metadata_json,
             branch_id, branch_key, version_no, role, kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                owner_user_id,
                scope_type,
                project_id,
                conversation_id,
                run_id,
                source_message_id,
                original_filename,
                stored_filename,
                mime_type,
                ext,
                size_bytes,
                sha256,
                self.storage.backend_name,
                self.storage.bucket,
                object_key,
                status,
                now,
                now,
                json.dumps(metadata or {}, ensure_ascii=False),
                branch["branch_id"],
                branch_key,
                version_no,
                role,
                kind,
            ),
        )
        if status == "ready":
            self._update_branch_latest(branch["branch_id"], asset_id, now)
        self.link(
            asset_id=asset_id,
            relation_type=relation_type,
            project_id=project_id,
            conversation_id=conversation_id,
            message_id=message_id,
            run_id=run_id,
            metadata=metadata or {},
            commit=False,
        )
        self.conn.commit()
        return self.get(asset_id)

    def link(
        self,
        *,
        asset_id: str,
        relation_type: str,
        project_id: str | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        link_id = short_id("link")
        created_at = now_iso()
        self.conn.execute(
            """
            INSERT INTO asset_links
            (link_id, asset_id, relation_type, project_id, conversation_id, message_id, run_id, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link_id,
                asset_id,
                relation_type,
                project_id,
                conversation_id,
                message_id,
                run_id,
                created_at,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        if commit:
            self.conn.commit()
        return {
            "link_id": link_id,
            "asset_id": asset_id,
            "relation_type": relation_type,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "run_id": run_id,
            "created_at": created_at,
            "metadata": metadata or {},
        }

    def get(self, asset_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return self._row_to_asset(row)

    def record_lineage(
        self,
        *,
        parent_asset_id: str,
        child_asset_id: str,
        relation: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO asset_lineage
            (parent_asset_id, child_asset_id, relation, run_id, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                parent_asset_id,
                child_asset_id,
                relation,
                run_id,
                now_iso(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def record_run_asset(
        self,
        *,
        run_id: str,
        asset_id: str,
        usage_type: str,
        local_path: str | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO run_assets
            (run_id, asset_id, usage_type, local_path, reason, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                asset_id,
                usage_type,
                local_path,
                reason,
                now_iso(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        if commit:
            self.conn.commit()

    def list_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT assets.* FROM assets
            JOIN asset_links ON asset_links.asset_id = assets.asset_id
            WHERE asset_links.conversation_id = ?
            ORDER BY assets.created_at, assets.asset_id
            """,
            (conversation_id,),
        ).fetchall()
        return [self._row_to_asset(row) for row in rows]

    def resolve_local_path(self, asset_id: str) -> Path:
        asset = self.get(asset_id)
        return self.storage.resolve_path(asset["object_key"])

    def materialize(self, asset_id: str, target_path: Path) -> None:
        asset = self.get(asset_id)
        if asset["status"] == "deleted":
            raise ValueError(f"asset {asset_id} object was deleted")
        self._validate_object_size(asset)
        self.storage.copy_to_path(asset["object_key"], target_path)
        self._validate_download(target_path, asset)

    def latest_for_branch(self, branch_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT assets.* FROM asset_branches
            JOIN assets ON assets.asset_id = asset_branches.latest_asset_id
            WHERE asset_branches.branch_id = ?
            """,
            (branch_id,),
        ).fetchone()
        return self._row_to_asset(row) if row is not None else None

    def accept_candidate_asset(self, asset_id: str, *, delete_previous_object: bool = True) -> dict[str, Any]:
        candidate = self.get(asset_id)
        if candidate["status"] == "deleted":
            raise ValueError("deleted asset cannot be accepted")
        if not candidate.get("branch_id"):
            raise ValueError("asset is not attached to a branch")
        now = now_iso()
        previous = self.latest_for_branch(str(candidate["branch_id"]))
        self.conn.execute(
            """
            UPDATE assets
            SET status = 'ready',
                kind = CASE WHEN kind = 'output' THEN 'material' ELSE kind END,
                role = CASE WHEN role = 'generated_output' THEN 'draft_doc' ELSE role END,
                accepted_at = COALESCE(accepted_at, ?),
                updated_at = ?
            WHERE asset_id = ?
            """,
            (now, now, asset_id),
        )
        self.conn.execute("UPDATE file_assets SET kind = 'material' WHERE file_id = ?", (asset_id,))
        self._update_branch_latest(str(candidate["branch_id"]), asset_id, now)
        if previous is not None and previous["asset_id"] != asset_id:
            self._retire_previous_asset(previous, now=now, delete_object=delete_previous_object)
        self.conn.commit()
        return self.get(asset_id)

    def reject_candidate_asset(self, asset_id: str, *, delete_object: bool = True) -> dict[str, Any]:
        asset = self.get(asset_id)
        if asset["status"] == "ready":
            raise ValueError("ready asset cannot be rejected")
        now = now_iso()
        if delete_object and asset["status"] != "deleted":
            self.storage.delete_object(asset["object_key"])
        self.conn.execute(
            """
            UPDATE assets
            SET status = 'rejected',
                deleted_at = COALESCE(deleted_at, ?),
                updated_at = ?
            WHERE asset_id = ?
            """,
            (now, now, asset_id),
        )
        self.conn.commit()
        return self.get(asset_id)

    def create_derivative_from_path(
        self,
        *,
        asset_id: str,
        derivative_type: str,
        source_path: Path,
        stored_filename: str,
        mime_type: str | None,
        size_bytes: int,
        sha256: str,
        metadata: dict[str, Any] | None = None,
        derivative_id: str | None = None,
    ) -> dict[str, Any]:
        asset = self.get(asset_id)
        derivative_id = derivative_id or short_id("derivative")
        object_key = f"{asset['object_key'].rsplit('/', 1)[0]}/derivatives/{derivative_id}/{stored_filename}"
        self.storage.put_path(source_path, object_key, mime_type)
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO asset_derivatives
            (derivative_id, asset_id, derivative_type, storage_backend, bucket, object_key,
             mime_type, size_bytes, sha256, status, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                derivative_id,
                asset_id,
                derivative_type,
                self.storage.backend_name,
                self.storage.bucket,
                object_key,
                mime_type,
                size_bytes,
                sha256,
                "ready",
                now,
                now,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return self.get_derivative(derivative_id)

    def get_derivative(self, derivative_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM asset_derivatives WHERE derivative_id = ?", (derivative_id,)).fetchone()
        if row is None:
            raise KeyError(derivative_id)
        return self._row_to_derivative(row)

    def find_derivative(self, asset_id: str, derivative_type: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM asset_derivatives
            WHERE asset_id = ? AND derivative_type = ? AND status = 'ready'
            ORDER BY created_at DESC, derivative_id DESC
            LIMIT 1
            """,
            (asset_id, derivative_type),
        ).fetchone()
        return self._row_to_derivative(row) if row is not None else None

    def materialize_derivative(self, derivative_id: str, target_path: Path) -> None:
        derivative = self.get_derivative(derivative_id)
        self._validate_object_size(derivative)
        self.storage.copy_to_path(derivative["object_key"], target_path)
        self._validate_download(target_path, derivative)

    def _retire_previous_asset(self, asset: dict[str, Any], *, now: str, delete_object: bool) -> None:
        status = "deleted" if delete_object else "superseded"
        if delete_object and asset["status"] != "deleted":
            self.storage.delete_object(asset["object_key"])
        self.conn.execute(
            """
            UPDATE assets
            SET status = ?,
                deleted_at = CASE WHEN ? = 'deleted' THEN COALESCE(deleted_at, ?) ELSE deleted_at END,
                updated_at = ?
            WHERE asset_id = ?
            """,
            (status, status, now, now, asset["asset_id"]),
        )

    def _validate_object_size(self, item: dict[str, Any]) -> None:
        expected = int(item.get("size_bytes") or 0)
        actual = self.storage.object_size(item["object_key"])
        if expected and actual != expected:
            raise ValueError(f"object size mismatch for {item['object_key']}: expected {expected}, got {actual}")

    def _validate_download(self, path: Path, item: dict[str, Any]) -> None:
        expected_size = int(item.get("size_bytes") or 0)
        actual_size = path.stat().st_size
        if expected_size and actual_size != expected_size:
            raise ValueError(f"downloaded file size mismatch for {item['object_key']}: expected {expected_size}, got {actual_size}")
        expected_hash = str(item.get("sha256") or "")
        if expected_hash:
            hasher = hashlib.sha256()
            with path.open("rb") as file:
                while True:
                    chunk = file.read(1024 * 1024)
                    if not chunk:
                        break
                    hasher.update(chunk)
            actual_hash = hasher.hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(f"downloaded file sha256 mismatch for {item['object_key']}")

    def _ensure_branch(
        self,
        *,
        owner_user_id: str,
        project_id: str | None,
        conversation_id: str | None,
        branch_key: str,
        asset_type: str,
    ) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT * FROM asset_branches
            WHERE owner_user_id = ?
              AND COALESCE(project_id, '') = COALESCE(?, '')
              AND COALESCE(conversation_id, '') = COALESCE(?, '')
              AND branch_key = ?
              AND asset_type = ?
            LIMIT 1
            """,
            (owner_user_id, project_id, conversation_id, branch_key, asset_type),
        ).fetchone()
        if row is not None:
            return dict(row)
        now = now_iso()
        branch_id = short_id("branch")
        self.conn.execute(
            """
            INSERT INTO asset_branches
            (branch_id, owner_user_id, project_id, conversation_id, branch_key, asset_type,
             latest_asset_id, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                branch_id,
                owner_user_id,
                project_id,
                conversation_id,
                branch_key,
                asset_type,
                None,
                now,
                now,
                json.dumps({}, ensure_ascii=False),
            ),
        )
        return {
            "branch_id": branch_id,
            "owner_user_id": owner_user_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "branch_key": branch_key,
            "asset_type": asset_type,
        }

    def _next_version_no(self, branch_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) AS max_version FROM assets WHERE branch_id = ?",
            (branch_id,),
        ).fetchone()
        return int(row["max_version"] or 0) + 1

    def _update_branch_latest(self, branch_id: str, asset_id: str, now: str) -> None:
        self.conn.execute(
            "UPDATE asset_branches SET latest_asset_id = ?, updated_at = ? WHERE branch_id = ?",
            (asset_id, now, branch_id),
        )

    def _normalize_branch_key(self, filename: str) -> str:
        stem = Path(filename).stem.lower()
        stem = re.sub(r"(最终版|最新版|副本|copy|final|latest)", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[_\-\s]*(v|version)?\d+(\.\d+)?$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[\(\（]\d+[\)\）]$", "", stem)
        stem = re.sub(r"\s+", "_", stem).strip("._- ")
        return stem or Path(filename).stem or "asset"

    def _asset_type(self, ext: str, metadata: dict[str, Any], relation_type: str) -> str:
        explicit = metadata.get("asset_type")
        if explicit:
            return str(explicit)
        if ext == ".docx":
            return "word_document"
        if ext in {".xlsx", ".xls", ".csv"}:
            return "spreadsheet"
        if relation_type == "guidance":
            return "guidance"
        if relation_type == "generated_output":
            return "generated_output"
        return "file"

    def _role_for(self, relation_type: str, ext: str) -> str:
        if relation_type == "generated_output":
            return "generated_output"
        if relation_type == "guidance":
            return "guidance"
        if ext == ".docx":
            return "draft_doc"
        if ext in {".xlsx", ".xls", ".csv"}:
            return "source_data"
        return "reference"

    def _kind_for(self, relation_type: str) -> str:
        if relation_type == "generated_output":
            return "output"
        if relation_type == "guidance":
            return "guidance"
        return "material"

    def _object_key(
        self,
        *,
        owner_user_id: str,
        scope_type: str,
        project_id: str | None,
        conversation_id: str | None,
        run_id: str | None,
        asset_id: str,
        filename: str,
    ) -> str:
        if scope_type == "project":
            scope = f"projects/{project_id or 'unassigned'}"
        elif scope_type == "conversation":
            scope = f"conversations/{conversation_id or 'unassigned'}"
        elif scope_type == "run":
            scope = f"conversations/{conversation_id or 'unassigned'}/runs/{run_id or 'unassigned'}"
        else:
            scope = "system"
        return f"users/{owner_user_id}/{scope}/assets/{asset_id}/{filename}"

    def _row_to_asset(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "asset_id": row["asset_id"],
            "owner_user_id": row["owner_user_id"],
            "scope_type": row["scope_type"],
            "project_id": row["project_id"],
            "conversation_id": row["conversation_id"],
            "run_id": row["run_id"] if "run_id" in row.keys() else None,
            "source_message_id": row["source_message_id"],
            "original_filename": row["original_filename"],
            "stored_filename": row["stored_filename"],
            "mime_type": row["mime_type"],
            "ext": row["ext"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "storage_backend": row["storage_backend"],
            "bucket": row["bucket"],
            "object_key": row["object_key"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "accepted_at": row["accepted_at"] if "accepted_at" in row.keys() else None,
            "deleted_at": row["deleted_at"] if "deleted_at" in row.keys() else None,
            "branch_id": row["branch_id"] if "branch_id" in row.keys() else None,
            "branch_key": row["branch_key"] if "branch_key" in row.keys() else None,
            "version_no": row["version_no"] if "version_no" in row.keys() else None,
            "role": row["role"] if "role" in row.keys() else None,
            "kind": row["kind"] if "kind" in row.keys() else None,
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def _row_to_derivative(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "derivative_id": row["derivative_id"],
            "asset_id": row["asset_id"],
            "derivative_type": row["derivative_type"],
            "storage_backend": row["storage_backend"],
            "bucket": row["bucket"],
            "object_key": row["object_key"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }
