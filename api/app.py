from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from mysql.connector import Error
from pydantic import BaseModel, Field

from api.agent import AgenticWorkflow
from api.utils.config import get_port
from api.utils.db import fetch_all
from api.utils.trimmer import get_trim_settings, update_trim_settings

log = logging.getLogger("autobase")
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

app = FastAPI(title="AutoBase API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = AgenticWorkflow()


class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default="default")


@app.post("/ping")
async def ping() -> dict[str, Any]:
    # Just a simple endpoint to check if the server is alive and the database connection works
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
        log.error("chat 400: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Error as exc:
        log.error("chat db error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        log.error("chat 500:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Chat workflow failed: {exc}") from exc


class ContextConfigRequest(BaseModel):
    max_messages: int | None = None
    max_response_tokens: int | None = None


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
