from fastapi import APIRouter

from app.core.config import settings
from app.models.catalog import catalog, default_model_id

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models")
async def models() -> dict[str, object]:
    selected_default = default_model_id(settings)
    return {
        "default": settings.model_provider,
        "default_model_id": selected_default,
        "models": [row | {"default": row["id"] == selected_default} for row in catalog(settings)],
        "providers": [
            {"name": "local", "model": settings.local_model_name},
            {
                "name": "cloud",
                "provider": settings.cloud_provider,
                "model": settings.gemini_model if settings.cloud_provider == "gemini" else settings.cloud_model,
            },
        ],
    }
