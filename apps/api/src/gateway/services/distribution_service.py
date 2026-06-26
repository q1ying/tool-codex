from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .asset_service import AssetService
from .file_planner import FilePlanner, row_to_candidate
from .path_security import safe_join


TEXT_DERIVATIVE_EXTENSIONS = {".docx", ".pdf", ".txt", ".md"}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xls", ".csv"}
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar"}


class DistributionService:
    def __init__(self, conn: sqlite3.Connection, asset_service: AssetService, file_planner: FilePlanner | None = None) -> None:
        self.conn = conn
        self.asset_service = asset_service
        self.file_planner = file_planner or FilePlanner()

    def build_plan(
        self,
        *,
        conversation_id: str,
        run_id: str,
        user_request: str,
        attachment_ids: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        attachment_ids = attachment_ids or []
        rows = self.conn.execute(
            """
            SELECT file_assets.file_id, file_assets.kind AS legacy_kind, file_assets.relative_path,
                   file_assets.metadata_json AS file_metadata_json,
                   assets.*
            FROM file_assets
            JOIN assets ON assets.asset_id = file_assets.file_id
            WHERE file_assets.conversation_id = ?
              AND (
                file_assets.kind = 'guidance'
                OR (
                  file_assets.kind = 'material'
                  AND file_assets.file_id IN ({placeholders})
                )
              )
              AND assets.status = 'ready'
            ORDER BY file_assets.created_at, file_assets.file_id
            """.format(placeholders=",".join("?" for _ in attachment_ids) or "''"),
            (conversation_id, *attachment_ids),
        ).fetchall()
        candidates = [row_to_candidate(row) for row in rows]
        return self.file_planner.plan(user_request=user_request, candidate_assets=candidates)

    def materialize_plan(self, *, session_root: Path, run_id: str, plan: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        manifest_files: list[dict[str, Any]] = []
        for item in plan.get("materialize", []):
            target = safe_join(session_root, item["target_path"])
            self.asset_service.materialize(item["asset_id"], target)
            item["local_path"] = item["target_path"]
            self.asset_service.record_run_asset(
                run_id=run_id,
                asset_id=item["asset_id"],
                usage_type="materialized",
                local_path=item["target_path"],
                reason=item.get("reason", ""),
                metadata={"source": item.get("source", {})},
                commit=False,
            )
            manifest_files.append(item)
        for usage_type in ("reference_only", "ignored"):
            for item in plan.get(usage_type, []):
                self.asset_service.record_run_asset(
                    run_id=run_id,
                    asset_id=item["asset_id"],
                    usage_type=usage_type,
                    reason=item.get("reason", ""),
                    metadata={"source": item.get("source", {})},
                    commit=False,
                )
        self.conn.commit()
        return manifest_files

    def _plan_row(self, row: sqlite3.Row) -> dict[str, Any]:
        ext = (row["ext"] or Path(row["stored_filename"]).suffix).lower()
        strategy = self._strategy_for(row["kind"], ext, int(row["size_bytes"] or 0))
        derivative = self.asset_service.find_derivative(row["file_id"], strategy["preferred_derivative"]) if strategy.get("preferred_derivative") else None
        if derivative is not None:
            mode = strategy["preferred_mode"]
            target_path = self._derivative_target_path(row, derivative)
            source = {
                "storage_backend": derivative["storage_backend"],
                "bucket": derivative["bucket"],
                "object_key": derivative["object_key"],
            }
            derivative_id = derivative["derivative_id"]
            size_bytes = derivative["size_bytes"]
            sha256 = derivative["sha256"]
        else:
            mode = "original"
            target_path = row["relative_path"]
            source = {
                "storage_backend": row["storage_backend"] or "legacy_workspace",
                "bucket": row["bucket"],
                "object_key": row["object_key"],
            }
            derivative_id = None
            size_bytes = row["size_bytes"]
            sha256 = row["sha256"]
        return {
            "asset_id": row["file_id"],
            "file_id": row["file_id"],
            "kind": row["kind"],
            "mode": mode,
            "strategy": strategy["name"],
            "reason": strategy["reason"] if derivative is not None else f"{strategy['reason']}; fallback=original",
            "target_path": target_path,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "original_filename": row["original_filename"],
            "derivative_id": derivative_id,
            "source": source,
        }

    def _strategy_for(self, kind: str, ext: str, size_bytes: int) -> dict[str, Any]:
        if ext in SPREADSHEET_EXTENSIONS:
            return {"name": "spreadsheet_original", "reason": "spreadsheets need structured workbook/csv access"}
        if ext in ARCHIVE_EXTENSIONS:
            return {
                "name": "archive_manifest_preferred",
                "reason": "archives can later be distributed as a file list or selected members",
                "preferred_mode": "manifest_only",
                "preferred_derivative": "archive_manifest",
            }
        if ext in TEXT_DERIVATIVE_EXTENSIONS and size_bytes > 2 * 1024 * 1024:
            return {
                "name": "large_document_text_preferred",
                "reason": "large documents can later use extracted text or chunks to reduce transfer",
                "preferred_mode": "extracted_text",
                "preferred_derivative": "extracted_text",
            }
        return {"name": "default_original", "reason": "no optimized derivative is available or needed"}

    def _derivative_target_path(self, row: sqlite3.Row, derivative: dict[str, Any]) -> str:
        base = Path(row["relative_path"])
        if derivative["derivative_type"] == "extracted_text":
            return (base.parent / f"{base.stem}.extracted.md").as_posix()
        if derivative["derivative_type"] == "archive_manifest":
            return (base.parent / f"{base.stem}.archive-manifest.md").as_posix()
        return (base.parent / f"{base.stem}.{derivative['derivative_type']}").as_posix()
