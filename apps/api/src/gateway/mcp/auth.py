from __future__ import annotations

from fastapi import Header, HTTPException

from ..dependencies import services


def authenticate_run(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        auth = services()["run_auth"].authenticate(token)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return str(auth["run_id"])
