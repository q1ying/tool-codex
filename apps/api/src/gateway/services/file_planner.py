from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SPREADSHEET_EXTS = {".xlsx", ".xls", ".csv"}
DOC_KEYWORDS = ("word", "docx", "文档", "报告", "专著", "修改", "更新", "插入", "写入")
SPREADSHEET_KEYWORDS = ("excel", "xlsx", "xls", "csv", "表格", "图表", "结果", "参数", "数据")


class FilePlanner:
    def plan(
        self,
        *,
        user_request: str,
        candidate_assets: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        request = user_request.lower()
        needs_docx = any(keyword in request for keyword in DOC_KEYWORDS)
        needs_spreadsheet = any(keyword in request for keyword in SPREADSHEET_KEYWORDS)
        materialize: list[dict[str, Any]] = []
        reference_only: list[dict[str, Any]] = []
        ignored: list[dict[str, Any]] = []

        guidance_assets = [item for item in candidate_assets if item.get("legacy_kind") == "guidance" or item.get("kind") == "guidance"]
        business_assets = [
            item
            for item in candidate_assets
            if item.get("legacy_kind") != "guidance" and item.get("kind") != "guidance"
        ]
        docx_assets = [item for item in business_assets if self._is_docx(item)]
        spreadsheet_assets = [item for item in business_assets if self._is_spreadsheet(item)]

        for item in guidance_assets:
            self._add_materialize(materialize, item, self._target_path(item), "user-uploaded SOP/writing rule/skill file")

        if needs_docx and docx_assets:
            latest_docx = self._latest(docx_assets)
            self._add_materialize(
                materialize,
                latest_docx,
                self._friendly_target(latest_docx, "latest_document"),
                "user request needs the latest editable Word/document file",
            )

        if needs_spreadsheet and spreadsheet_assets:
            latest_sheet = self._latest(spreadsheet_assets)
            self._add_materialize(
                materialize,
                latest_sheet,
                self._friendly_target(latest_sheet, "model_result"),
                "user request needs spreadsheet or chart data",
            )

        if not any(item.get("kind") == "material" for item in materialize):
            for item in self._latest_many(business_assets, limit=3):
                self._add_materialize(materialize, item, self._target_path(item), "fallback: no stronger file intent matched")

        materialized_ids = {item["asset_id"] for item in materialize}
        for item in candidate_assets:
            if item["asset_id"] in materialized_ids:
                continue
            if self._is_large_spreadsheet(item):
                reference_only.append(self._plan_item(item, None, "large spreadsheet kept in object storage until needed"))
            else:
                ignored.append(self._plan_item(item, None, "not selected by current request"))

        return {
            "materialize": materialize,
            "reference_only": reference_only,
            "ignored": ignored,
        }

    def _add_materialize(self, plan: list[dict[str, Any]], asset: dict[str, Any], target_path: str, reason: str) -> None:
        if any(item["asset_id"] == asset["asset_id"] for item in plan):
            return
        plan.append(self._plan_item(asset, target_path, reason))

    def _plan_item(self, asset: dict[str, Any], target_path: str | None, reason: str) -> dict[str, Any]:
        return {
            "asset_id": asset["asset_id"],
            "file_id": asset.get("file_id") or asset["asset_id"],
            "original_filename": asset["original_filename"],
            "stored_filename": asset["stored_filename"],
            "relative_path": asset["relative_path"],
            "target_path": target_path,
            "mime_type": asset.get("mime_type"),
            "ext": asset.get("ext") or Path(asset["stored_filename"]).suffix.lower(),
            "size_bytes": asset.get("size_bytes") or 0,
            "sha256": asset.get("sha256"),
            "branch_id": asset.get("branch_id"),
            "branch_key": asset.get("branch_key"),
            "version_no": asset.get("version_no"),
            "role": asset.get("role"),
            "kind": asset.get("legacy_kind") or asset.get("kind"),
            "reason": reason,
            "source": {
                "storage_backend": asset.get("storage_backend"),
                "bucket": asset.get("bucket"),
                "object_key": asset.get("object_key"),
            },
        }

    def _target_path(self, asset: dict[str, Any]) -> str:
        return asset["relative_path"]

    def _friendly_target(self, asset: dict[str, Any], stem: str) -> str:
        ext = asset.get("ext") or Path(asset["stored_filename"]).suffix.lower()
        folder = {"guidance": "guidance"}.get(asset.get("kind"), "materials")
        return f"{folder}/{stem}{ext}"

    def _latest(self, assets: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(assets, key=lambda item: (item.get("version_no") or 0, item.get("created_at") or ""), reverse=True)[0]

    def _latest_many(self, assets: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        return sorted(assets, key=lambda item: item.get("created_at") or "", reverse=True)[:limit]

    def _is_docx(self, asset: dict[str, Any]) -> bool:
        return asset.get("mime_type") == DOCX_MIME or (asset.get("ext") or "").lower() == ".docx"

    def _is_spreadsheet(self, asset: dict[str, Any]) -> bool:
        return (asset.get("ext") or "").lower() in SPREADSHEET_EXTS

    def _is_large_spreadsheet(self, asset: dict[str, Any]) -> bool:
        return self._is_spreadsheet(asset) and int(asset.get("size_bytes") or 0) > 5 * 1024 * 1024


def row_to_candidate(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    metadata = data.get("metadata_json")
    if metadata:
        data.update(json.loads(metadata or "{}"))
    return data
