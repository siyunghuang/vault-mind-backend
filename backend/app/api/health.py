from fastapi import APIRouter

from app.core.config import settings
from app.mcp.client import status as mcp_status
from app.models.catalog import default_model_id
from app.models.factory import get_provider

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    provider = get_provider()
    return {
        "ok": True,
        "provider": settings.model_provider,
        "model_id": default_model_id(settings),
        "provider_status": await provider.health(),
        "mcp": await mcp_status(),
    }
