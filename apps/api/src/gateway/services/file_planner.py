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

        planned_candidates = [self._candidate_item(item, user_request=user_request) for item in candidate_assets]
        candidate_by_id = {item["asset_id"]: item for item in planned_candidates}
        guidance_assets = [item for item in candidate_assets if item.get("legacy_kind") == "guidance" or item.get("kind") == "guidance"]
        business_assets = [
            item
            for item in candidate_assets
            if item.get("legacy_kind") != "guidance" and item.get("kind") != "guidance"
        ]
        docx_assets = [item for item in business_assets if self._is_docx(item)]
        spreadsheet_assets = [item for item in business_assets if self._is_spreadsheet(item)]

        for item in guidance_assets:
            self._add_materialize(materialize, item, self._target_path(item), "用户上传的 SOP、写作规则或 skill 文件")

        if needs_docx and docx_assets:
            latest_docx = self._latest(docx_assets)
            self._add_materialize(
                materialize,
                latest_docx,
                self._friendly_target(latest_docx, "latest_document"),
                "用户请求需要最新可编辑 Word/文档文件",
            )

        if needs_spreadsheet and spreadsheet_assets:
            latest_sheet = self._latest(spreadsheet_assets)
            self._add_materialize(
                materialize,
                latest_sheet,
                self._friendly_target(latest_sheet, "model_result"),
                "用户请求需要表格、数据或图表文件",
            )

        if not any(item.get("kind") == "material" for item in materialize):
            for item in self._latest_many(business_assets, limit=3):
                self._add_materialize(materialize, item, self._target_path(item), "兜底候选：没有匹配到更明确的文件意图")

        materialized_ids = {item["asset_id"] for item in materialize}
        for item in candidate_assets:
            if item["asset_id"] in materialized_ids:
                continue
            if self._is_large_spreadsheet(item):
                reference_only.append(self._plan_item(item, None, "大型表格暂留在对象存储，按需访问"))
            else:
                ignored.append(self._plan_item(item, None, "本次请求未选中"))

        for item in materialize:
            if item["asset_id"] in candidate_by_id:
                candidate_by_id[item["asset_id"]]["selected_mode"] = "materialize"
                candidate_by_id[item["asset_id"]]["target_path"] = item.get("target_path")
                candidate_by_id[item["asset_id"]]["why_included"] = item.get("reason") or candidate_by_id[item["asset_id"]]["why_included"]
        for item in reference_only:
            if item["asset_id"] in candidate_by_id:
                candidate_by_id[item["asset_id"]]["selected_mode"] = "deferred"
                candidate_by_id[item["asset_id"]]["why_included"] = item.get("reason") or candidate_by_id[item["asset_id"]]["why_included"]
        for item in ignored:
            if item["asset_id"] in candidate_by_id:
                candidate_by_id[item["asset_id"]]["selected_mode"] = "ignored"
                candidate_by_id[item["asset_id"]]["why_included"] = item.get("reason") or candidate_by_id[item["asset_id"]]["why_included"]

        return {
            "candidate_assets": list(candidate_by_id.values()),
            "materialize": materialize,
            "reference_only": reference_only,
            "ignored": ignored,
        }

    def _add_materialize(self, plan: list[dict[str, Any]], asset: dict[str, Any], target_path: str, reason: str) -> None:
        if any(item["asset_id"] == asset["asset_id"] for item in plan):
            return
        plan.append(self._plan_item(asset, target_path, reason))

    def _plan_item(self, asset: dict[str, Any], target_path: str | None, reason: str) -> dict[str, Any]:
        metadata = _metadata(asset)
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
            "summary": self._summary(asset, metadata=metadata),
            "why_included": reason,
            "source": {
                "storage_backend": asset.get("storage_backend"),
                "bucket": asset.get("bucket"),
                "object_key": asset.get("object_key"),
            },
        }

    def _candidate_item(self, asset: dict[str, Any], *, user_request: str) -> dict[str, Any]:
        metadata = _metadata(asset)
        reason = self._candidate_reason(asset, user_request=user_request)
        return {
            "asset_id": asset["asset_id"],
            "file_id": asset.get("file_id") or asset["asset_id"],
            "filename": asset["original_filename"],
            "original_filename": asset["original_filename"],
            "kind": asset.get("legacy_kind") or asset.get("kind"),
            "role": asset.get("role"),
            "mime_type": asset.get("mime_type"),
            "ext": asset.get("ext") or Path(asset["stored_filename"]).suffix.lower(),
            "size_bytes": asset.get("size_bytes") or 0,
            "sha256": asset.get("sha256"),
            "branch_id": asset.get("branch_id"),
            "branch_key": asset.get("branch_key"),
            "version_no": asset.get("version_no"),
            "summary": self._summary(asset, metadata=metadata),
            "why_included": reason,
            "selected_mode": "candidate",
            "target_path": None,
            "source": {
                "storage_backend": asset.get("storage_backend"),
                "bucket": asset.get("bucket"),
                "object_key": asset.get("object_key"),
            },
        }

    def _summary(self, asset: dict[str, Any], *, metadata: dict[str, Any]) -> str:
        description = str(metadata.get("description") or "").strip()
        if description:
            return description
        name = asset.get("original_filename") or asset.get("stored_filename") or asset.get("asset_id")
        kind = asset.get("legacy_kind") or asset.get("kind") or "asset"
        ext = asset.get("ext") or Path(str(asset.get("stored_filename") or "")).suffix.lower() or "unknown"
        role = asset.get("role") or self._role_hint(ext)
        size = int(asset.get("size_bytes") or 0)
        return f"{name}，类型={kind}，角色={role}，扩展名={ext}，大小={size} bytes。"

    def _candidate_reason(self, asset: dict[str, Any], *, user_request: str) -> str:
        kind = asset.get("legacy_kind") or asset.get("kind")
        if kind == "guidance":
            return "后端管理的规则、SOP 或 skill 文件，默认作为候选。"
        name = str(asset.get("original_filename") or "").lower()
        request = user_request.lower()
        ext = (asset.get("ext") or Path(str(asset.get("stored_filename") or "")).suffix).lower()
        if ext == ".docx" and any(keyword in request for keyword in DOC_KEYWORDS):
            return "用户请求包含文档/报告/Word 相关意图。"
        if ext in SPREADSHEET_EXTS and any(keyword in request for keyword in SPREADSHEET_KEYWORDS):
            return "用户请求包含表格/数据/图表相关意图。"
        if any(token and token in request for token in Path(name).stem.replace("_", " ").split()):
            return "文件名关键词与用户请求匹配。"
        return "属于当前用户和当前 conversation 的可用资产。"

    def _role_hint(self, ext: str) -> str:
        if ext == ".docx":
            return "document"
        if ext in SPREADSHEET_EXTS:
            return "spreadsheet"
        if ext in {".md", ".txt", ".pdf"}:
            return "reference"
        return "file"

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


def _metadata(asset: dict[str, Any]) -> dict[str, Any]:
    raw = asset.get("metadata") or asset.get("metadata_json") or asset.get("file_metadata_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}
