from __future__ import annotations

from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..dependencies import services

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _object_storage_unavailable(exc: Exception) -> HTTPException:
    endpoint = get_settings().object_storage_endpoint
    return HTTPException(
        status_code=503,
        detail=(
            f"Object storage is unavailable at {endpoint}. "
            "Start MinIO with `docker compose -f docker-compose.minio.yml up -d` "
            "or update OBJECT_STORAGE_ENDPOINT, then retry. "
            f"Original error: {exc}"
        ),
    )


def _record_upload_failed(conversation_id: str, kind: str, filename: str, exc: Exception) -> None:
    services()["events"].append(
        conversation_id,
        "file_upload_failed",
        f"Failed to upload {filename} to object storage.",
        level="error",
        payload={
            "kind": kind,
            "filename": filename,
            "object_storage_endpoint": get_settings().object_storage_endpoint,
            "error": str(exc),
        },
    )


def _conversation_for_submit(svc: dict, user_id: str, title: str, conversation_id: str | None) -> dict:
    if conversation_id:
        return svc["conversations"].get(conversation_id)
    existing = svc["conversations"].latest_for_user(user_id)
    if existing is not None:
        return existing
    return svc["conversations"].create(user_id=user_id, title=title, user_request="")


def _material_file_ids(svc: dict, conversation_id: str) -> list[str]:
    # TODO: replace this broad fallback with intent-based asset selection.
    return [
        item["file_id"]
        for item in svc["files"].list(conversation_id)
        if item["kind"] == "material" and item.get("asset_status") in {None, "ready"}
    ]


def _upload_message_content(asset: dict) -> str:
    kind_label = "素材" if asset["kind"] == "material" else "规则"
    return f"上传{kind_label}文件：{asset['original_filename']}"


async def _save_form_uploads(
    *,
    svc: dict,
    conversation_id: str,
    user_id: str,
    uploads: list,
    kind: str,
    description: str = "",
) -> list[dict]:
    saved_files = []
    for upload in uploads:
        if not hasattr(upload, "filename") or not hasattr(upload, "file"):
            continue
        filename = upload.filename or "upload.bin"
        try:
            saved_files.append(
                svc["files"].save_upload(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    kind=kind,
                    original_filename=filename,
                    content_type=upload.content_type,
                    fileobj=upload.file,
                    description=description,
                )
            )
        except (BotoCoreError, ClientError) as exc:
            _record_upload_failed(conversation_id, kind, filename, exc)
            raise _object_storage_unavailable(exc) from exc
        finally:
            if hasattr(upload, "close"):
                await upload.close()
    return saved_files


class CreateConversationRequest(BaseModel):
    title: str = Field(min_length=1)
    user_request: str = ""


class AddMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    attachment_ids: list[str] = []


class StartRunRequest(BaseModel):
    base_version_id: str | None = None
    user_instruction: str | None = None


class CandidateDecisionRequest(BaseModel):
    delete_object: bool = True


@router.post("")
def create_conversation(request: CreateConversationRequest) -> dict:
    svc = services()
    try:
        return svc["conversations"].create(
            user_id=get_settings().default_user_id,
            title=request.title,
            user_request=request.user_request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_conversations() -> dict:
    return {"items": services()["conversations"].list()}


@router.get("/current")
def get_current_conversation() -> dict:
    svc = services()
    settings = get_settings()
    try:
        conv = svc["conversations"].get(settings.default_conversation_id)
    except KeyError:
        conv = svc["conversations"].latest_for_user(settings.default_user_id)
    return {"conversation": conv}


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    try:
        return services()["conversations"].get(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc


@router.post("/{conversation_id}/messages")
def add_message(conversation_id: str, request: AddMessageRequest) -> dict:
    try:
        services()["conversations"].get(conversation_id)
        return services()["conversations"].add_user_message(conversation_id, request.content, request.attachment_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc


@router.post("/{conversation_id}/files")
async def upload_file(
    conversation_id: str,
    file: UploadFile = File(...),
    kind: str = Form("material"),
    description: str = Form(""),
) -> dict:
    svc = services()
    try:
        conv = svc["conversations"].get(conversation_id)
        saved = await _save_form_uploads(
            svc=svc,
            conversation_id=conversation_id,
            user_id=conv["user_id"],
            uploads=[file],
            kind=kind,
            description=description,
        )
        asset = saved[0]
        svc["conversations"].add_user_message(conversation_id, _upload_message_content(asset), [asset["file_id"]])
        return asset
    except (BotoCoreError, ClientError) as exc:
        _record_upload_failed(conversation_id, kind, file.filename or "upload.bin", exc)
        raise _object_storage_unavailable(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{conversation_id}/files")
def list_files(conversation_id: str) -> dict:
    try:
        services()["conversations"].get(conversation_id)
        return {"items": services()["files"].list(conversation_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc


@router.get("/{conversation_id}/file-branches")
def list_file_branches(conversation_id: str) -> dict:
    try:
        services()["conversations"].get(conversation_id)
        return {"items": services()["files"].branch_summary(conversation_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc


@router.get("/files/{file_id}/download", response_model=None)
def download_file(file_id: str) -> Response:
    svc = services()
    try:
        asset = svc["files"].get(file_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    try:
        durable_asset = svc["assets"].get(file_id)
        if durable_asset["status"] in {"deleted", "rejected"}:
            raise HTTPException(status_code=410, detail=f"file object is {durable_asset['status']}")
        filename = asset["original_filename"]
        quoted = quote(filename)
        return StreamingResponse(
            svc["storage"].iter_bytes(durable_asset["object_key"]),
            media_type=asset["mime_type"] or "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
        )
    except KeyError:
        path = svc["files"].resolve_asset_path(file_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="stored file missing")
        return FileResponse(
            path,
            filename=asset["original_filename"],
            media_type=asset["mime_type"] or "application/octet-stream",
        )


@router.post("/files/{file_id}/accept")
def accept_candidate_file(file_id: str, request: CandidateDecisionRequest) -> dict:
    svc = services()
    try:
        accepted = svc["assets"].accept_candidate_asset(file_id, delete_previous_object=request.delete_object)
        svc["events"].append(
            accepted["conversation_id"],
            "asset_candidate_accepted",
            f"Accepted candidate asset {file_id}.",
            run_id=accepted.get("run_id"),
            payload={"asset_id": file_id, "branch_id": accepted.get("branch_id")},
        )
        return {"asset": accepted}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/files/{file_id}/reject")
def reject_candidate_file(file_id: str, request: CandidateDecisionRequest) -> dict:
    svc = services()
    try:
        rejected = svc["assets"].reject_candidate_asset(file_id, delete_object=request.delete_object)
        svc["events"].append(
            rejected["conversation_id"],
            "asset_candidate_rejected",
            f"Rejected candidate asset {file_id}.",
            run_id=rejected.get("run_id"),
            payload={"asset_id": file_id, "branch_id": rejected.get("branch_id")},
        )
        return {"asset": rejected}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{conversation_id}/runs")
def start_run(conversation_id: str, request: StartRunRequest, background_tasks: BackgroundTasks) -> dict:
    svc = services()
    try:
        svc["conversations"].get(conversation_id)
        queued = svc["runs"].queue_run(
            conversation_id,
            request.user_instruction,
            request.base_version_id,
            attachment_ids=_material_file_ids(svc, conversation_id),
        )
        background_tasks.add_task(svc["runs"].execute_run, conversation_id, queued["run_id"], request.base_version_id)
        return queued
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc


@router.get("/{conversation_id}/runs")
def list_runs(conversation_id: str) -> dict:
    try:
        services()["conversations"].get(conversation_id)
        return {"items": services()["runs"].list(conversation_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc


@router.get("/runs/{run_id}/runtime")
def get_run_runtime(run_id: str) -> dict:
    try:
        return services()["runs"].runtime(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.get("/runs/{run_id}/session")
def get_run_session(run_id: str) -> dict:
    try:
        return services()["sessions"].get_by_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@router.get("/{conversation_id}/events")
def list_events(conversation_id: str) -> dict:
    try:
        services()["conversations"].get(conversation_id)
        return {"items": services()["events"].list(conversation_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc


@router.post("/run")
async def submit_and_run(
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict:
    svc = services()
    settings = get_settings()
    user_id = settings.default_user_id
    try:
        form = await request.form()
        user_instruction = str(form.get("user_instruction") or "").strip()
        if not user_instruction:
            raise ValueError("user_instruction is required")
        title = str(form.get("title") or "整理原始数据为规范 Excel 附件")
        conversation_id = str(form.get("conversation_id") or "").strip() or None
        conv = _conversation_for_submit(
            svc,
            user_id=user_id,
            title=title,
            conversation_id=conversation_id,
        )
        saved_files = await _save_form_uploads(
            svc=svc,
            conversation_id=conv["conversation_id"],
            user_id=user_id,
            uploads=form.getlist("files"),
            kind="material",
        )
        attachment_ids = list(dict.fromkeys([*_material_file_ids(svc, conv["conversation_id"]), *[item["file_id"] for item in saved_files]]))
        queued = svc["runs"].queue_run(
            conv["conversation_id"],
            user_instruction,
            attachment_ids=attachment_ids,
        )
        background_tasks.add_task(svc["runs"].execute_run, conv["conversation_id"], queued["run_id"], None)
        return {
            "conversation": svc["conversations"].get(conv["conversation_id"]),
            "uploaded_files": saved_files,
            "run": queued,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
