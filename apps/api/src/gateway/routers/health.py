from fastapi import APIRouter

from ..config import get_settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "asset_mcp_url": settings.asset_mcp_url,
    }
