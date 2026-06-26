from fastapi import APIRouter

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/ssh-check")
def ssh_check() -> dict:
    return {
        "status": "skipped",
        "message": "SSH device checks are managed per device. Use POST /api/devices/{device_id}/health-check.",
    }
