from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import re

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .config import get_settings
from .db import connect, init_db
from .routers import assets, conversations, devices, health, system
from .services.asset_service import AssetService
from .services.distribution_service import DistributionService
from .services.session_workspace_service import SessionWorkspaceService
from .services.storage_service import StorageService

LOCAL_ORIGIN_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$")
SESSION_CLEANUP_INTERVAL_SECONDS = 60 * 60


def _allowed_origin(origin: str | None) -> str | None:
    if not origin:
        return None
    if LOCAL_ORIGIN_RE.match(origin):
        return origin
    return None


def _apply_cors_headers(response: Response, origin: str | None) -> Response:
    allowed = _allowed_origin(origin)
    if allowed:
        response.headers["Access-Control-Allow-Origin"] = allowed
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Max-Age"] = "600"
    return response


def _cleanup_expired_sessions() -> int:
    settings = get_settings()
    init_db(settings.database_path)
    with connect(settings.database_path) as conn:
        storage = StorageService(settings)
        assets_service = AssetService(conn, storage)
        distribution = DistributionService(conn, assets_service)
        sessions = SessionWorkspaceService(conn, settings.data_dir, distribution)
        return len(sessions.cleanup_expired())


async def _session_cleanup_loop() -> None:
    while True:
        try:
            cleaned = await asyncio.to_thread(_cleanup_expired_sessions)
            if cleaned:
                print(f"cleaned {cleaned} expired session(s)")
        except Exception as exc:
            print(f"expired session cleanup failed: {exc}")
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(_session_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


def create_app() -> FastAPI:
    settings = get_settings()
    init_db(settings.database_path)
    app = FastAPI(title="Codex Workspace Gateway", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def cors_fallback_middleware(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method == "OPTIONS":
            return _apply_cors_headers(Response(status_code=204), origin)
        try:
            response = await call_next(request)
        except Exception as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=500)
        return _apply_cors_headers(response, origin)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(assets.router)
    app.include_router(devices.router)
    app.include_router(conversations.router)
    return app


app = create_app()
