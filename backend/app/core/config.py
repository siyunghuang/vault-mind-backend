from dataclasses import dataclass
import os


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_payloads: bool = _bool("LOG_PAYLOADS")
    log_payload_chars: int = int(os.getenv("LOG_PAYLOAD_CHARS", "1000"))
    chat_mcp_max_rounds: int = int(os.getenv("CHAT_MCP_MAX_ROUNDS", "3"))
    model_provider: str = os.getenv("MODEL_PROVIDER", "local")
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    cloud_provider: str = os.getenv("CLOUD_PROVIDER", "nvidia")
    nvidia_api_key: str = os.getenv("NVIDIA_API_KEY", "")
    nvidia_base_url: str = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    cloud_model: str = os.getenv("CLOUD_MODEL", "minimaxai/minimax-m3")
    nvidia_models: str = os.getenv("NVIDIA_MODELS", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_base_url: str = os.getenv(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
    )
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    gemini_models: str = os.getenv("GEMINI_MODELS", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    openai_models: str = os.getenv("OPENAI_MODELS", "gpt-5.4-mini")
    local_model_endpoint: str = os.getenv("LOCAL_MODEL_ENDPOINT", "http://127.0.0.1:11434")
    local_model_name: str = os.getenv("LOCAL_MODEL_NAME", "llama3")
    local_models: str = os.getenv("LOCAL_MODELS", "")
    default_model_id: str = os.getenv("DEFAULT_MODEL_ID", "")
    mcp_server_command: str = os.getenv("MCP_SERVER_COMMAND", "")
    mcp_server_url: str = os.getenv("MCP_SERVER_URL", "")
    mcp_api_key: str = os.getenv("MCP_API_KEY", "")
    mcp_verify_ssl: bool = _bool("MCP_VERIFY_SSL")
    mcp_required: bool = _bool("MCP_REQUIRED")
    chat_mcp_enabled: bool = _bool("CHAT_MCP_ENABLED")

    def __post_init__(self) -> None:
        if self.model_provider not in {"cloud", "local"}:
            raise ValueError("MODEL_PROVIDER must be 'cloud' or 'local'")
        if self.cloud_provider not in {"nvidia", "gemini", "openai"}:
            raise ValueError("CLOUD_PROVIDER must be 'nvidia', 'gemini', or 'openai'")
        if self.chat_mcp_max_rounds < 1:
            raise ValueError("CHAT_MCP_MAX_ROUNDS must be at least 1")


settings = Settings()
