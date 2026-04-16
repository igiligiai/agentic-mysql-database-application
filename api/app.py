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
    openai_api_key: str | None = None
    model: str = Field(default="gpt-4o")


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
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/tool")
async def tool_dispatch(payload: ToolRequest) -> dict[str, Any]:
    try:
        return agent.run_tool(payload.tool, payload.args)

    except HTTPException:
        raise
    except Error as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/chat")
async def chat(payload: ChatRequest) -> dict[str, Any]:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    try:
        return agent.run_chat(
            user_message=payload.message,
            session_id=payload.session_id,
            openai_api_key=payload.openai_api_key,
            model=payload.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Error as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat workflow failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.app:app", host="0.0.0.0", port=get_port(), reload=True)
