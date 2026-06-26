from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..dependencies import services

router = APIRouter(prefix="/api", tags=["assets"])


class ArtifactUploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1)
    content_type: str | None = None
    role: str = "artifact"


class ArtifactCompleteRequest(BaseModel):
    object_key: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    size_bytes: int | None = None
    sha256: str | None = None
    content_type: str | None = None
    role: str = "artifact"
    parent_asset_ids: list[str] = []


class SearchAssetsRequest(BaseModel):
    query: str = ""
    limit: int = 20


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


@router.get("/runs/{run_id}/asset-candidates")
def list_run_asset_candidates(run_id: str) -> dict[str, Any]:
    try:
        return {"items": services()["asset_access"].list_candidate_assets(run_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/runs/{run_id}/assets/search")
def search_run_assets(run_id: str, request: SearchAssetsRequest) -> dict[str, Any]:
    try:
        return {"items": services()["asset_access"].search_assets(run_id, request.query, limit=request.limit)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.get("/runs/{run_id}/assets/{asset_id}/chunks/{chunk_index}")
def read_asset_chunk(run_id: str, asset_id: str, chunk_index: int, chunk_size: int = 8192) -> dict[str, Any]:
    try:
        return services()["asset_access"].read_asset_chunk(
            run_id,
            asset_id,
            chunk_index=chunk_index,
            chunk_size=chunk_size,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run or asset not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/assets/{asset_id}/download-url")
def create_asset_download_url(run_id: str, asset_id: str) -> dict[str, Any]:
    try:
        return services()["asset_access"].download_url(run_id, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run or asset not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/runs/{run_id}/artifacts/upload-url")
def create_artifact_upload_url(run_id: str, request: ArtifactUploadUrlRequest) -> dict[str, Any]:
    try:
        return services()["asset_access"].upload_url(
            run_id,
            filename=request.filename,
            content_type=request.content_type,
            role=request.role,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/runs/{run_id}/artifacts/complete")
def complete_artifact(run_id: str, request: ArtifactCompleteRequest) -> dict[str, Any]:
    try:
        return services()["asset_access"].complete_artifact(
            run_id,
            object_key=request.object_key,
            filename=request.filename,
            size_bytes=request.size_bytes,
            sha256=request.sha256,
            content_type=request.content_type,
            role=request.role,
            parent_asset_ids=request.parent_asset_ids,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run or asset not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
