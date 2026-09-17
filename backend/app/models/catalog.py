from typing import Any

from app.core.config import Settings
from app.models.base import ProviderError


def _split(value: str, fallback: str) -> list[str]:
    models = [item.strip() for item in value.split(",") if item.strip()]
    return models or [fallback]


def catalog(settings: Settings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model in _split(settings.local_models, settings.local_model_name):
        rows.append({"id": f"local:{model}", "provider": "local", "model": model, "available": True})
    for model in _split(settings.nvidia_models, settings.cloud_model):
        rows.append(
            {"id": f"nvidia:{model}", "provider": "nvidia", "model": model, "available": bool(settings.nvidia_api_key)}
        )
    for model in _split(settings.gemini_models, settings.gemini_model):
        rows.append(
            {"id": f"gemini:{model}", "provider": "gemini", "model": model, "available": bool(settings.gemini_api_key)}
        )
    for model in _split(settings.openai_models, settings.openai_model):
        rows.append(
            {"id": f"openai:{model}", "provider": "openai", "model": model, "available": bool(settings.openai_api_key)}
        )
    return rows


def default_model_id(settings: Settings) -> str:
    if settings.default_model_id:
        return settings.default_model_id
    if settings.model_provider == "local":
        return f"local:{settings.local_model_name}"
    if settings.cloud_provider == "gemini":
        model = settings.gemini_model
    elif settings.cloud_provider == "openai":
        model = settings.openai_model
    else:
        model = settings.cloud_model
    return f"{settings.cloud_provider}:{model}"


def resolve_model(settings: Settings, provider: str | None = None, model_id: str | None = None) -> dict[str, Any]:
    target = model_id
    if not target:
        if provider == "local":
            target = f"local:{settings.local_model_name}"
        elif provider == "cloud":
            if settings.cloud_provider == "gemini":
                model = settings.gemini_model
            elif settings.cloud_provider == "openai":
                model = settings.openai_model
            else:
                model = settings.cloud_model
            target = f"{settings.cloud_provider}:{model}"
        else:
            target = default_model_id(settings)

    for row in catalog(settings):
        if row["id"] == target:
            return row
    raise ProviderError(f"model not allowed: {target}", code="model_not_allowed", status_code=400)
