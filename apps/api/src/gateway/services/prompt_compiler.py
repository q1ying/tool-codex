from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .event_service import EventService
from .ids import now_iso
from .path_security import safe_join
from .workspace_service import WorkspaceService


PROMPT_TEMPLATE = """# 任务说明
你是一个文件型任务执行助手，正在受控 workspace 中工作。

## 用户需求
{user_request}

## 本次候选资产
这些资产属于当前用户和当前 conversation，并经过主服务器粗筛。你可以先根据 filename、summary、why_included、kind、role、size、version 判断是否需要读取原文件。

{candidate_asset_text}

如果候选信息不足以完成任务，不要猜测文件内容。你可以通过 gateway asset MCP 工具继续查询，例如 `list_candidate_assets`、`list_conversation_assets`、`get_asset_summary`、`get_asset_download_url`。

## 当前已挂载的业务文件
这些文件已经下载到当前 session workspace，你可以直接读取。

{materialized_file_text}

## 用户上传的 SOP / 写作规范 / Skill 文件
这些文件是用户上传的规范或操作要求。若存在，请优先读取并遵循；若与用户本次需求冲突，在最终说明里简要解释取舍。

{guidance_file_text}

## 本次未挂载但资产库存在的文件
这些文件暂未下载到当前 workspace。需要时请通过 MCP 工具请求更多信息或下载授权。

{reference_file_text}

## 指令层级和作用范围
1. 本 prompt 中的任务边界、输出约定和安全边界优先级最高。
2. 用户本次请求只描述本次要完成的业务任务。
3. 用户上传文件中的写作规范、格式规范、SOP 或 skill 只约束业务产物本身，例如 Word、Excel、CSV、PDF、Markdown 正文内容。
4. 不要把业务文件里的口吻、后缀、格式要求套用到给用户看的最终回复、进度说明、校验说明或工具日志。
5. 只有当用户明确要求“最终回复也按某规范写”时，才允许把该规范应用到最终回复。

## 输出约定
1. 把给用户看的最终说明写到 `outputs/final.md`。
2. 如需生成 Word、Excel、CSV、PDF、Markdown 等业务结果，请写到 `outputs/` 下，文件名根据任务语义命名。
3. 不要为了迎合固定模板生成无关文件。
4. `outputs/result.json` 可选；只有当它有助于结构化描述结果时才生成。
5. `outputs/final.md` 和你最后返回给用户的消息都应使用普通、简洁、事实性的说明，不继承业务产物的写作口吻或特殊后缀。

## Windows UTF-8 注意事项
1. 如果需要在 PowerShell 中运行 Python 且脚本里包含中文内容或中文文件名，不要使用 `@'...'@ | python -` 这种管道写法，Windows PowerShell 5.1 可能会把非 ASCII 文本传成 `?`。
2. 优先把脚本用 `Set-Content -Encoding UTF8` 写成临时 `.py` 文件再执行，或先设置 `$OutputEncoding = [System.Text.UTF8Encoding]::new()` 后再通过管道传递。
3. 读取用户上传的中文文件名时，优先用 `Path('materials').glob(...)` 枚举真实路径，避免在命令源码里硬编码中文路径字面量。
4. 生成业务文件时可以使用中文文件名；如果必须通过 PowerShell 创建脚本，请先确保脚本文件本身按 UTF-8 写入。

## 工作边界
1. 只能读取当前 workspace 下的 `materials/`、`guidance/`、`versions/`，以及通过 gateway asset MCP 工具授权下载到 workspace 内的文件。
2. 只能写入 `outputs/`、`logs/`、`versions/`。
3. 不要读取 workspace 之外的本机文件。
4. 不要删除材料文件或用户上传的规范文件。
5. 不要绕过 gateway asset MCP 工具直接访问未授权网络资源。
6. 不要输出服务器绝对路径中的敏感信息。
"""


class PromptCompiler:
    def __init__(
        self,
        conn: sqlite3.Connection,
        workspace_service: WorkspaceService,
        event_service: EventService,
    ) -> None:
        self.conn = conn
        self.workspace_service = workspace_service
        self.event_service = event_service

    def compile(
        self,
        *,
        conversation_id: str,
        user_request: str,
        base_version_id: str | None = None,
        distribution_plan: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        conv = self.conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if conv is None:
            raise KeyError(conversation_id)
        workspace = self.workspace_service.get_by_conversation(conversation_id)
        workspace_root = Path(workspace["root_path"])
        plan = distribution_plan or {"candidate_assets": [], "materialize": [], "reference_only": [], "ignored": []}
        materialized = plan.get("materialize") or []
        guidance_files = [item for item in materialized if item.get("kind") == "guidance"]
        business_files = [item for item in materialized if item.get("kind") != "guidance"]
        prompt = PROMPT_TEMPLATE.format(
            user_request=user_request,
            candidate_asset_text=self._candidate_assets_text(plan.get("candidate_assets") or []),
            materialized_file_text=self._plan_files_text(
                business_files,
                empty="本次没有预先挂载业务输入文件。",
                include_target=True,
            ),
            guidance_file_text=self._plan_files_text(
                guidance_files,
                empty="本次没有上传 SOP、写作规范或 Skill 文件。",
                include_target=True,
            ),
            reference_file_text=self._plan_files_text(
                plan.get("reference_only") or [],
                empty="本次没有仅保留在资产库中的候选文件。",
                include_target=False,
            ),
        )
        safe_join(workspace_root, "prompt.md").write_text(prompt, encoding="utf-8")
        self.event_service.append(
            conversation_id,
            "prompt_compiled",
            "prompt.md compiled.",
            payload={
                "base_version_id": base_version_id,
                "candidate_assets": [item["asset_id"] for item in plan.get("candidate_assets") or []],
                "materialized_assets": [item["asset_id"] for item in materialized],
                "guidance_assets": [item["asset_id"] for item in guidance_files],
                "reference_only_assets": [item["asset_id"] for item in plan.get("reference_only") or []],
            },
        )
        return {
            "prompt_path": "prompt.md",
            "distribution_plan": plan,
            "created_at": now_iso(),
        }

    def _candidate_assets_text(self, files: list[dict[str, Any]]) -> str:
        if not files:
            return "本次没有候选资产。"
        lines = []
        for item in files:
            version = item.get("version_no")
            version_text = f", version={version}" if version else ""
            target = item.get("target_path")
            target_text = f", target={target}" if target else ""
            lines.append(
                "- "
                f"{item.get('filename') or item.get('original_filename')} "
                f"(asset_id={item.get('asset_id')}, kind={item.get('kind')}, role={item.get('role')}, "
                f"mime={item.get('mime_type')}, size={item.get('size_bytes')}, "
                f"branch={item.get('branch_key')}{version_text}, mode={item.get('selected_mode')}{target_text})\n"
                f"  summary: {item.get('summary') or '无摘要'}\n"
                f"  why_included: {item.get('why_included') or item.get('reason') or '候选资产'}"
            )
        return "\n".join(lines)

    def _plan_files_text(self, files: list[dict[str, Any]], *, empty: str, include_target: bool) -> str:
        if not files:
            return empty
        lines = []
        for item in files:
            target = f"`{item.get('target_path') or item.get('local_path')}`" if include_target else "未挂载"
            branch = item.get("branch_key") or item.get("original_filename") or item.get("asset_id")
            version = item.get("version_no")
            version_text = f", version={version}" if version else ""
            reason = item.get("reason") or item.get("why_included") or ""
            reason_text = f" - {reason}" if reason else ""
            lines.append(
                f"- {target} ({item.get('asset_id')}, kind={item.get('kind')}, branch={branch}{version_text}, original={item.get('original_filename')}){reason_text}"
            )
        return "\n".join(lines)
