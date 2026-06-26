from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..dependencies import services

router = APIRouter(prefix="/api", tags=["assets"])


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str) -> dict[str, Any]:
    try:
        return services()["assets"].get(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="asset not found") from exc


@router.get("/conversations/{conversation_id}/assets")
def list_conversation_assets(conversation_id: str) -> dict[str, Any]:
    svc = services()
    try:
        svc["conversations"].get(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    return {"items": svc["assets"].list_for_conversation(conversation_id)}
