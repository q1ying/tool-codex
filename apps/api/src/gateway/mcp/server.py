from __future__ import annotations

import sqlite3
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..config import get_settings
from ..db import connect
from ..services.asset_access_service import AssetAccessService
from ..services.asset_service import AssetService
from ..services.run_auth_service import RunAuthService, token_expires_at_epoch
from ..services.storage_service import StorageService

ASSET_MCP_TOOLS = [
    "list_tools",
    "get_run_context",
    "list_candidate_assets",
    "list_conversation_assets",
    "search_assets",
    "get_asset_summary",
    "read_asset_chunk",
    "get_asset_download_url",
    "get_artifact_upload_url",
    "complete_artifact",
    "report_progress",
]


class AssetMCPTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        settings = get_settings()
        conn = connect(settings.database_path)
        try:
            record = RunAuthService(conn).verify_token(token)
            if record is None:
                return None
            return AccessToken(
                token=record["token_id"],
                client_id=record["run_id"],
                scopes=["asset:read", "asset:write"],
                expires_at=token_expires_at_epoch(record["expires_at"]),
                resource=settings.asset_mcp_url,
            )
        finally:
            conn.close()


def create_asset_mcp_app() -> tuple[FastMCP, Any]:
    settings = get_settings()
    auth = AuthSettings(
        issuer_url=settings.asset_mcp_url,
        resource_server_url=settings.asset_mcp_url,
        required_scopes=["asset:read"],
    )
    asset_mcp = FastMCP(
        name="codex-gateway-assets",
        instructions=(
            "Access run-scoped Gateway assets. Use get_run_context first, "
            "then list_candidate_assets or search_assets before requesting chunks or URLs."
        ),
        token_verifier=AssetMCPTokenVerifier(),
        auth=auth,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=_transport_security(settings.asset_mcp_url),
    )
    _register_tools(asset_mcp)
    return asset_mcp, asset_mcp.streamable_http_app()


def _register_tools(asset_mcp: FastMCP) -> None:
    @asset_mcp.tool(structured_output=True)
    def list_tools() -> dict[str, Any]:
        """List Gateway asset MCP tool names."""
        return {"tools": ASSET_MCP_TOOLS}

    @asset_mcp.tool(structured_output=True)
    def get_run_context() -> dict[str, Any]:
        """Return the run and conversation context bound to this bearer token."""
        with _asset_access() as access:
            return access.service.get_run_context(_current_run_id())

    @asset_mcp.tool(structured_output=True)
    def list_candidate_assets() -> dict[str, Any]:
        """List assets selected as candidates for this run."""
        with _asset_access() as access:
            return {"items": access.service.list_candidate_assets(_current_run_id())}

    @asset_mcp.tool(structured_output=True)
    def list_conversation_assets() -> dict[str, Any]:
        """List non-deleted assets in this run's conversation."""
        with _asset_access() as access:
            return {"items": access.service.list_conversation_assets(_current_run_id())}

    @asset_mcp.tool(structured_output=True)
    def search_assets(query: str = "", limit: int = 20) -> dict[str, Any]:
        """Search candidate and conversation assets by filename, summary, kind, role, and metadata."""
        with _asset_access() as access:
            return {"items": access.service.search_assets(_current_run_id(), query, limit=limit)}

    @asset_mcp.tool(structured_output=True)
    def get_asset_summary(asset_id: str) -> dict[str, Any]:
        """Return metadata, summary, and available derivatives for one authorized asset."""
        with _asset_access() as access:
            return access.service.get_asset_summary(_current_run_id(), asset_id)

    @asset_mcp.tool(structured_output=True)
    def read_asset_chunk(asset_id: str, chunk_index: int = 0, chunk_size: int = 8192) -> dict[str, Any]:
        """Read a bounded byte chunk from an authorized asset."""
        with _asset_access() as access:
            return access.service.read_asset_chunk(
                _current_run_id(),
                asset_id,
                chunk_index=chunk_index,
                chunk_size=chunk_size,
            )

    @asset_mcp.tool(structured_output=True)
    def get_asset_download_url(asset_id: str) -> dict[str, Any]:
        """Create a short-lived download URL for an authorized asset."""
        with _asset_access() as access:
            return access.service.download_url(_current_run_id(), asset_id)

    @asset_mcp.tool(structured_output=True)
    def get_artifact_upload_url(filename: str, content_type: str | None = None, role: str = "artifact") -> dict[str, Any]:
        """Create a scoped upload URL for an artifact produced by this run."""
        with _asset_access() as access:
            return access.service.upload_url(
                _current_run_id(),
                filename=filename,
                content_type=content_type,
                role=role,
            )

    @asset_mcp.tool(structured_output=True)
    def complete_artifact(
        object_key: str,
        filename: str,
        size_bytes: int | None = None,
        sha256: str | None = None,
        content_type: str | None = None,
        role: str = "artifact",
        parent_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a previously uploaded artifact as a run output candidate."""
        with _asset_access() as access:
            return access.service.complete_artifact(
                _current_run_id(),
                object_key=object_key,
                filename=filename,
                size_bytes=size_bytes,
                sha256=sha256,
                content_type=content_type,
                role=role,
                parent_asset_ids=parent_asset_ids or [],
            )

    @asset_mcp.tool(structured_output=True)
    def report_progress(message: str, progress: float | None = None, total: float | None = None, level: str = "info") -> dict[str, Any]:
        """Record a progress event on the bound run."""
        with _asset_access() as access:
            return access.service.report_progress(
                _current_run_id(),
                message=message,
                progress=progress,
                total=total,
                level=level,
            )


class _AssetAccessContext:
    def __init__(self) -> None:
        self.conn: sqlite3.Connection | None = None
        self.service: AssetAccessService

    def __enter__(self) -> "_AssetAccessContext":
        settings = get_settings()
        self.conn = connect(settings.database_path)
        storage = StorageService(settings)
        assets = AssetService(self.conn, storage)
        self.service = AssetAccessService(self.conn, assets)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.conn is not None:
            self.conn.close()


def _asset_access() -> _AssetAccessContext:
    return _AssetAccessContext()


def _current_run_id() -> str:
    token = get_access_token()
    if token is None or not token.client_id:
        raise PermissionError("asset MCP bearer token is missing or invalid")
    return token.client_id


def _transport_security(asset_mcp_url: str) -> TransportSecuritySettings:
    parsed = urlparse(asset_mcp_url)
    hosts = {"127.0.0.1:*", "localhost:*", "[::1]:*"}
    origins = {"http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"}
    if parsed.netloc:
        hosts.add(parsed.netloc)
    if parsed.hostname:
        hosts.add(f"{parsed.hostname}:*")
        origin_scheme = parsed.scheme or "http"
        origins.add(f"{origin_scheme}://{parsed.hostname}:*")
    if parsed.scheme and parsed.netloc:
        origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=sorted(origins),
    )
