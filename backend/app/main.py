import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.mcp import router as mcp_router
from app.api.models import router as models_router
from app.core.config import settings
from app.core.diagnostics import RequestIdFilter

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=settings.log_level.upper(),
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    force=True,
)
formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)
for logger_name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
    for handler in logging.getLogger(logger_name).handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RequestIdFilter())
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)

app = FastAPI(title="Vault Mind Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

app.include_router(chat_router)
app.include_router(health_router)
app.include_router(mcp_router)
app.include_router(models_router)
