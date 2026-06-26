from __future__ import annotations

from typing import Any

from ..dependencies import services


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_tools",
        "description": "列出 gateway asset MCP 当前提供的工具。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_run_context",
        "description": "读取当前 bearer token 对应的 run、conversation、user 和 MCP 上下文。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_candidate_assets",
        "description": "列出主服务器为当前 run 粗筛出的候选资产。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_conversation_assets",
        "description": "列出当前 run 所属用户和 conversation 下的全部 ready assets。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_assets",
        "description": "在当前 conversation 的 ready assets 中做关键词搜索。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_asset_summary",
        "description": "读取某个 asset 的摘要和 metadata。",
        "inputSchema": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
        },
    },
    {
        "name": "read_asset_chunk",
        "description": "按字节块读取当前 run 允许访问的 asset 内容；主要适合文本或已抽取文本的文件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "chunk_index": {"type": "integer"},
                "chunk_size": {"type": "integer"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "get_asset_download_url",
        "description": "为当前 run 允许访问的 asset 生成短期对象存储下载 URL。",
        "inputSchema": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
        },
    },
    {
        "name": "get_artifact_upload_url",
        "description": "为当前 run 的输出 artifact 生成短期对象存储上传 URL。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content_type": {"type": "string"},
                "role": {"type": "string"},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "complete_artifact",
        "description": "Codex 直传 artifact 到对象存储后，调用此工具让 gateway 正式登记输出资产。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "object_key": {"type": "string"},
                "filename": {"type": "string"},
                "size_bytes": {"type": "integer"},
                "sha256": {"type": "string"},
                "content_type": {"type": "string"},
                "role": {"type": "string"},
                "parent_asset_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["object_key", "filename"],
        },
    },
    {
        "name": "report_progress",
        "description": "向 gateway 报告当前 run 的进度。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["status"],
        },
    },
]


def call_tool(run_id: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
    arguments = arguments or {}
    access = services()["asset_access"]
    if name == "list_tools":
        return {"tools": TOOL_DEFINITIONS}
    if name == "get_run_context":
        return access.run_context(run_id)
    if name == "list_candidate_assets":
        return {"items": access.list_candidate_assets(run_id)}
    if name == "list_conversation_assets":
        return {"items": access.list_conversation_assets(run_id)}
    if name == "search_assets":
        return {
            "items": access.search_assets(
                run_id,
                str(arguments["query"]),
                limit=int(arguments.get("limit") or 20),
            )
        }
    if name == "get_asset_summary":
        return access.get_asset_summary(run_id, str(arguments["asset_id"]))
    if name == "read_asset_chunk":
        return access.read_asset_chunk(
            run_id,
            str(arguments["asset_id"]),
            chunk_index=int(arguments.get("chunk_index") or 0),
            chunk_size=int(arguments.get("chunk_size") or 8192),
        )
    if name == "get_asset_download_url":
        return access.download_url(run_id, str(arguments["asset_id"]))
    if name == "get_artifact_upload_url":
        return access.upload_url(
            run_id,
            filename=str(arguments["filename"]),
            content_type=arguments.get("content_type"),
            role=str(arguments.get("role") or "artifact"),
        )
    if name == "complete_artifact":
        return access.complete_artifact(
            run_id,
            object_key=str(arguments["object_key"]),
            filename=str(arguments["filename"]),
            size_bytes=int(arguments["size_bytes"]) if arguments.get("size_bytes") is not None else None,
            sha256=arguments.get("sha256"),
            content_type=arguments.get("content_type"),
            role=str(arguments.get("role") or "artifact"),
            parent_asset_ids=list(arguments.get("parent_asset_ids") or []),
        )
    if name == "report_progress":
        return access.report_progress(
            run_id,
            status=str(arguments["status"]),
            message=str(arguments.get("message") or ""),
        )
    raise KeyError(f"unknown tool: {name}")
