from app.core.config import settings
from app.models.base import ModelProvider
from app.models.catalog import resolve_model
from app.models.cloud_provider import CloudProvider
from app.models.local_provider import LocalProvider


def get_provider(name: str | None = None, model_id: str | None = None) -> ModelProvider:
    selected = resolve_model(settings, name, model_id)
    if selected["provider"] == "local":
        return LocalProvider(settings, selected["model"])
    if selected["provider"] in {"gemini", "nvidia"}:
        return CloudProvider(settings, selected["provider"], selected["model"])
    raise ValueError("provider must be 'cloud' or 'local'")
