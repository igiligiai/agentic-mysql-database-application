from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mysql.connector import Error
from pydantic import BaseModel, Field

from api.agent import AgenticWorkflow
from api.utils.config import FRONT_DIR, get_port
from api.utils.db import fetch_all
from api.utils import telemetry
from api.utils.errors import from_mysql_error, is_tool_error, redact
from api.utils.trimmer import get_trim_settings, update_trim_settings

app = FastAPI(title="AutoBase API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.isdir(FRONT_DIR):
    app.mount("/front", StaticFiles(directory=FRONT_DIR), name="front")

agent = AgenticWorkflow()


class ToolRequest(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default="default")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Any:
    icon_path = FRONT_DIR / "favicon.ico"
    if icon_path.is_file():
        return FileResponse(icon_path)
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/")
async def serve_frontend() -> Any:
    index_path = FRONT_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return {"ok": True, "message": "AutoBase API is running."}


@app.post("/ping")
async def ping() -> dict[str, Any]:
    try:
        tables = fetch_all(
            """
            SELECT
              TABLE_NAME AS name,
              TABLE_TYPE AS type,
              TABLE_ROWS AS row_count
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
            ORDER BY TABLE_TYPE, TABLE_NAME
            """
        )
        return {"ok": True, "tables": tables}
    except Error as exc:
        raise HTTPException(status_code=500, detail=from_mysql_error(exc)["error"]) from exc


@app.post("/tool")
async def tool_dispatch(payload: ToolRequest) -> dict[str, Any]:
    try:
        result = agent.run_tool(payload.tool, payload.args)
    except HTTPException:
        raise
    except Error as exc:
        raise HTTPException(status_code=500, detail=from_mysql_error(exc)["error"]) from exc

    # Handlers return ToolError dicts so the agent can repair itself. The
    # non-LLM frontend callers (Schema Explorer, Lineage Graph) expect HTTP
    # errors, so this is the single conversion point.
    if is_tool_error(result):
        status = 400 if result["code"] in {"SQL_FORBIDDEN", "TOOL_BAD_ARGS"} else 500
        raise HTTPException(status_code=status, detail=result["error"])

    return result


@app.post("/chat")
async def chat(payload: ChatRequest) -> dict[str, Any]:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    try:
        return agent.run_chat(
            user_message=payload.message,
            session_id=payload.session_id,
        )
    except ValueError as exc:
        # ValueError can originate anywhere inside agent.invoke() (langchain/
        # openai/json internals), so it is redacted exactly like every other
        # exception arm here -- never str(exc) verbatim.
        raise HTTPException(status_code=400, detail=redact(str(exc))) from exc
    except HTTPException:
        raise
    except Error as exc:
        raise HTTPException(status_code=500, detail=from_mysql_error(exc)["error"]) from exc
    except Exception as exc:
        # Never forward str(exc) here: an uncaught exception may carry driver
        # text, a traceback fragment, or anything else from deep inside the
        # agent loop. Only the redacted ToolError contract may reach a client.
        raise HTTPException(
            status_code=500, detail="Chat workflow failed unexpectedly."
        ) from exc


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    """Aggregate per-turn telemetry. Shapes and numbers only, no transcripts."""
    return telemetry.snapshot()


class ContextConfigRequest(BaseModel):
    max_messages: int | None = None
    max_tool_result_chars: int | None = None
    keep_last_n_pairs: int | None = None
    trim_enabled: bool | None = None


@app.get("/context-config")
async def get_context_config() -> dict[str, Any]:
    return get_trim_settings()


@app.post("/context-config")
async def set_context_config(payload: ContextConfigRequest) -> dict[str, Any]:
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    return update_trim_settings(patch)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.app:app", host="0.0.0.0", port=get_port(), reload=True)
