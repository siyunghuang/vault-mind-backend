from typing import Any

from fastapi import APIRouter, HTTPException

from app.mcp.client import McpError, call_tool, list_tools

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/tools")
async def tools() -> dict[str, object]:
    try:
        return {"tools": await list_tools()}
    except McpError as exc:
        raise HTTPException(status_code=exc.status_code, detail={
            "message": str(exc), "code": exc.code, "status_code": exc.status_code,
        }) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/tools/{tool_name}")
async def run_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return await call_tool(tool_name, arguments)
    except McpError as exc:
        raise HTTPException(status_code=exc.status_code, detail={
            "message": str(exc), "code": exc.code, "status_code": exc.status_code,
        }) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
