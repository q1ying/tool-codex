from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..dependencies import services
from ..services.remote_check_service import check_device_codex
from ..services.remote_workspace_service import cleanup_remote_sessions

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _model_data(model: BaseModel, *, exclude_unset: bool = False) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=exclude_unset)
    return model.dict(exclude_unset=exclude_unset)


class DeviceCreate(BaseModel):
    device_id: str | None = None
    name: str = "Codex Device"
    runner_mode: str = "local"
    status: str = "enabled"
    host: str = ""
    user: str = ""
    port: int = 22
    remote_root: str = "~/lqy/codex-workspaces"
    codex_executable: str = ""
    local_executable: str = ""
    ssh_identity_file: str = ""
    ssh_auth_method: str = "key"
    ssh_password: str = Field(default="", repr=False)
    ssh_command_prefix: str = ""
    ssh_strict_host_key_checking: str = "yes"
    sandbox_mode: str = "workspace-write"
    disable_external_mcps: bool = True
    config_overrides: list[str] = Field(default_factory=list)
    max_concurrent_runs: int = 1
    weight: int = 100
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeviceUpdate(BaseModel):
    name: str | None = None
    runner_mode: str | None = None
    status: str | None = None
    host: str | None = None
    user: str | None = None
    port: int | None = None
    remote_root: str | None = None
    codex_executable: str | None = None
    local_executable: str | None = None
    ssh_identity_file: str | None = None
    ssh_auth_method: str | None = None
    ssh_password: str | None = Field(default=None, repr=False)
    ssh_command_prefix: str | None = None
    ssh_strict_host_key_checking: str | None = None
    sandbox_mode: str | None = None
    disable_external_mcps: bool | None = None
    config_overrides: list[str] | None = None
    max_concurrent_runs: int | None = None
    weight: int | None = None
    metadata: dict[str, Any] | None = None


class CleanupRemoteSessionsRequest(BaseModel):
    older_than_minutes: int = 60


@router.get("")
def list_devices() -> dict[str, Any]:
    return {"items": services()["devices"].list()}


@router.post("")
def create_device(payload: DeviceCreate) -> dict[str, Any]:
    try:
        return services()["devices"].create(_model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{device_id}")
def get_device(device_id: str) -> dict[str, Any]:
    try:
        return services()["devices"].get(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found.") from exc


@router.patch("/{device_id}")
def update_device(device_id: str, payload: DeviceUpdate) -> dict[str, Any]:
    data = _model_data(payload, exclude_unset=True)
    try:
        return services()["devices"].update(device_id, data)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{device_id}")
def delete_device(device_id: str) -> dict[str, Any]:
    try:
        services()["devices"].delete(device_id)
        return {"deleted": True, "device_id": device_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found.") from exc


@router.post("/{device_id}/health-check")
def health_check_device(device_id: str) -> dict[str, Any]:
    svc = services()["devices"]
    try:
        device = svc.get(device_id, include_secret=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found.") from exc
    result = check_device_codex(device)
    svc.record_health(device_id, result.get("status", "failed"), result.get("message", ""))
    result["device_id"] = device_id
    return result


@router.post("/{device_id}/remote-sessions/cleanup")
def cleanup_remote_device_sessions(device_id: str, payload: CleanupRemoteSessionsRequest) -> dict[str, Any]:
    svc = services()["devices"]
    try:
        device = svc.get(device_id, include_secret=True)
        result = cleanup_remote_sessions(device, older_than_minutes=payload.older_than_minutes)
        result["device_id"] = device_id
        result["remote_root"] = device.get("remote_root")
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
