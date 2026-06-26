from __future__ import annotations

import sqlite3

from .config import get_settings
from .db import connect
from .services.asset_service import AssetService
from .services.conversation_service import ConversationService
from .services.device_service import DeviceService
from .services.distribution_service import DistributionService
from .services.event_service import EventService
from .services.file_service import FileService
from .services.prompt_compiler import PromptCompiler
from .services.run_service import RunService
from .services.session_workspace_service import SessionWorkspaceService
from .services.storage_service import StorageService
from .services.workspace_service import WorkspaceService


def get_conn() -> sqlite3.Connection:
    return connect(get_settings().database_path)


def services() -> dict:
    settings = get_settings()
    conn = get_conn()
    device_service = DeviceService(conn, settings)
    storage_service = StorageService(settings)
    asset_service = AssetService(conn, storage_service)
    distribution_service = DistributionService(conn, asset_service)
    session_service = SessionWorkspaceService(conn, settings.data_dir, distribution_service)
    event_service = EventService(conn)
    workspace_service = WorkspaceService(conn, settings.data_dir)
    conversation_service = ConversationService(conn, workspace_service, event_service)
    prompt_compiler = PromptCompiler(conn, workspace_service, event_service)
    file_service = FileService(conn, workspace_service, event_service, asset_service)
    return {
        "conn": conn,
        "settings": settings,
        "devices": device_service,
        "storage": storage_service,
        "assets": asset_service,
        "distribution": distribution_service,
        "sessions": session_service,
        "events": event_service,
        "workspaces": workspace_service,
        "conversations": conversation_service,
        "files": file_service,
        "prompt_compiler": prompt_compiler,
        "runs": RunService(conn, settings, conversation_service, workspace_service, event_service, prompt_compiler, file_service, device_service, session_service),
    }
