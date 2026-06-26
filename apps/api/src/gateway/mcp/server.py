from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

from ..config import get_settings
from ..dependencies import services
from .tools import TOOL_DEFINITIONS, call_tool

_current_run_id: ContextVar[str | None] = ContextVar("asset_mcp_run_id", default=None)


class AuthenticatedAssetMcpApp:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        run_id = self._authenticate(scope)
        if run_id is None:
            await _send_json(send, 401, {"detail": "missing or invalid bearer token"})
            return
        token = _current_run_id.set(run_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_run_id.reset(token)

    def _authenticate(self, scope: dict[str, Any]) -> str | None:
        authorization = _header(scope, b"authorization")
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        bearer_token = authorization.split(" ", 1)[1].strip()
        try:
            auth = services()["run_auth"].authenticate(bearer_token)
        except PermissionError:
            return None
        return str(auth["run_id"])


def _run_id() -> str:
    run_id = _current_run_id.get()
    if not run_id:
        raise PermissionError("asset MCP request is not bound to a run")
    return run_id


def create_asset_mcp_app() -> tuple[FastMCP, AuthenticatedAssetMcpApp]:
    asset_mcp = FastMCP(
        "codex-gateway-assets",
        instructions="Run-scoped gateway asset tools for Codex Workspace Gateway.",
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=_allowed_hosts()),
    )
    _register_tools(asset_mcp)
    return asset_mcp, AuthenticatedAssetMcpApp(asset_mcp.streamable_http_app())


def _register_tools(asset_mcp: FastMCP) -> None:
    @asset_mcp.tool(name="list_tools", description="列出 gateway asset MCP 当前提供的工具。")
    def list_tools() -> dict[str, Any]:
        return {"tools": TOOL_DEFINITIONS}

    @asset_mcp.tool(name="get_run_context", description="读取当前 bearer token 对应的 run、conversation、user 和 MCP 上下文。")
    def get_run_context() -> dict[str, Any]:
        return call_tool(_run_id(), "get_run_context", {})

    @asset_mcp.tool(name="list_candidate_assets", description="列出主服务器为当前 run 粗筛出的候选资产。")
    def list_candidate_assets() -> dict[str, Any]:
        return call_tool(_run_id(), "list_candidate_assets", {})

    @asset_mcp.tool(name="list_conversation_assets", description="列出当前 run 所属用户和 conversation 下的全部 ready assets。")
    def list_conversation_assets() -> dict[str, Any]:
        return call_tool(_run_id(), "list_conversation_assets", {})

    @asset_mcp.tool(name="search_assets", description="在当前 conversation 的 ready assets 中做关键词搜索。")
    def search_assets(query: str, limit: int = 20) -> dict[str, Any]:
        return call_tool(_run_id(), "search_assets", {"query": query, "limit": limit})

    @asset_mcp.tool(name="get_asset_summary", description="读取某个 asset 的摘要和 metadata。")
    def get_asset_summary(asset_id: str) -> dict[str, Any]:
        return call_tool(_run_id(), "get_asset_summary", {"asset_id": asset_id})

    @asset_mcp.tool(name="read_asset_chunk", description="按字节块读取当前 run 允许访问的 asset 内容；主要适合文本或已抽取文本的文件。")
    def read_asset_chunk(asset_id: str, chunk_index: int = 0, chunk_size: int = 8192) -> dict[str, Any]:
        return call_tool(
            _run_id(),
            "read_asset_chunk",
            {"asset_id": asset_id, "chunk_index": chunk_index, "chunk_size": chunk_size},
        )

    @asset_mcp.tool(name="get_asset_download_url", description="为当前 run 允许访问的 asset 生成短期对象存储下载 URL。")
    def get_asset_download_url(asset_id: str) -> dict[str, Any]:
        return call_tool(_run_id(), "get_asset_download_url", {"asset_id": asset_id})

    @asset_mcp.tool(name="get_artifact_upload_url", description="为当前 run 的输出 artifact 生成短期对象存储上传 URL。")
    def get_artifact_upload_url(filename: str, content_type: str | None = None, role: str = "artifact") -> dict[str, Any]:
        return call_tool(
            _run_id(),
            "get_artifact_upload_url",
            {"filename": filename, "content_type": content_type, "role": role},
        )

    @asset_mcp.tool(name="complete_artifact", description="Codex 直传 artifact 到对象存储后，调用此工具让 gateway 正式登记输出资产。")
    def complete_artifact(
        object_key: str,
        filename: str,
        size_bytes: int | None = None,
        sha256: str | None = None,
        content_type: str | None = None,
        role: str = "artifact",
        parent_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            _run_id(),
            "complete_artifact",
            {
                "object_key": object_key,
                "filename": filename,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "content_type": content_type,
                "role": role,
                "parent_asset_ids": parent_asset_ids or [],
            },
        )

    @asset_mcp.tool(name="report_progress", description="向 gateway 报告当前 run 的进度。")
    def report_progress(status: str, message: str = "") -> dict[str, Any]:
        return call_tool(_run_id(), "report_progress", {"status": status, "message": message})


def _header(scope: dict[str, Any], name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


async def _send_json(send: Any, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _allowed_hosts() -> list[str]:
    settings = get_settings()
    parsed = urlparse(settings.asset_mcp_url)
    hosts = {
        "testserver",
        "127.0.0.1",
        "127.0.0.1:8010",
        "localhost",
        "localhost:8010",
    }
    if parsed.netloc:
        hosts.add(parsed.netloc)
    if parsed.hostname:
        hosts.add(parsed.hostname)
    return sorted(hosts)
