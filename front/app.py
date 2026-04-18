"""Streamlit frontend for AutoBase.

Communicates with the FastAPI backend exclusively via HTTP — no shared imports.

Start backend:  uv run python main.py
Start frontend: uv run streamlit run front/app.py
"""
from __future__ import annotations

import os

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="AutoBase", page_icon="🗄️", layout="wide")

# ── Session state ────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages: list[dict] = []
if "session_id" not in st.session_state:
    st.session_state.session_id = "default"

# ── Sidebar — Context Window Tuning ─────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Context Window")

    @st.cache_data(show_spinner=False)
    def _load_ctx() -> dict:
        try:
            r = requests.get(f"{BACKEND_URL}/context-config", timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {"max_messages": 40, "max_response_tokens": 1024}

    ctx = _load_ctx()

    max_response_tokens = st.slider(
        "Max response tokens", min_value=64, max_value=8192, step=64,
        value=int(ctx.get("max_response_tokens", 1024)),
    )
    max_messages = st.slider(
        "Max messages", min_value=4, max_value=200, value=int(ctx.get("max_messages", 40))
    )

    if st.button("Apply", use_container_width=True):
        try:
            r = requests.post(
                f"{BACKEND_URL}/context-config",
                json={
                    "max_response_tokens": max_response_tokens,
                    "max_messages": max_messages,
                },
                timeout=5,
            )
            r.raise_for_status()
            st.cache_data.clear()
            st.success("Saved")
        except Exception as exc:
            st.error(f"Failed: {exc}")

# ── Main — Chat interface ────────────────────────────────────────────────────
st.title("🗄️ AutoBase")

# Render conversation history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        for call in msg.get("tool_calls", []):
            with st.expander(f"🔧 {call['name']}", expanded=False):
                st.json({"args": call.get("args"), "result": call.get("result")})

# Chat input
if prompt := st.chat_input("Ask about your database…"):
    # Show user message immediately
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call backend
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                resp = requests.post(
                    f"{BACKEND_URL}/chat",
                    json={"message": prompt, "session_id": st.session_state.session_id},
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                reply = data.get("reply", "")
                tool_calls = data.get("tool_calls", [])
                st.session_state.session_id = data.get("session_id", st.session_state.session_id)
            except requests.exceptions.ConnectionError:
                reply = "⚠️ Cannot reach backend. Is `uv run python main.py` running?"
                tool_calls = []
            except Exception as exc:
                reply = f"⚠️ Error: {exc}"
                tool_calls = []

        st.markdown(reply)
        for call in tool_calls:
            with st.expander(f"🔧 {call['name']}", expanded=False):
                st.json({"args": call.get("args"), "result": call.get("result")})

    st.session_state.messages.append(
        {"role": "assistant", "content": reply, "tool_calls": tool_calls}
    )
