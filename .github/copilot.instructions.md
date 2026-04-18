# AutoBase — Copilot Development Instructions

Comprehensive guidelines for developing, extending, and improving the AutoBase agentic MySQL application. Read this file before making any changes.

---

## 1. Project Overview

AutoBase is a **Python-first agentic MySQL application**. It connects to a MySQL database and exposes a conversational agent that translates natural language into SQL and explores the schema.

### Core philosophy

- The **backend owns all intelligence**: OpenAI calls, tool execution, and agent state live exclusively in Python. The frontend is a thin HTTP client.
- **Tool-first design**: every capability the agent has is a `@tool`-decorated function defined inside `build_tools()` in `helpers.py`. The DB logic lives inline in the tool — no separate handler layer.
- **SELECT-only safety**: the `execute_sql` tool returns an error JSON string for non-SELECT input. Never raise an exception from inside a `@tool` — exceptions escape LangGraph and surface as HTTP errors.
- **Stateful sessions**: conversation memory is persisted per `session_id` via LangGraph's `InMemorySaver` checkpointer. Context is trimmed using `RemoveMessage` before each invocation.
- **Frontend isolation**: the Streamlit frontend communicates with the FastAPI backend exclusively via HTTP. It never imports from the `api/` package.

---

## 2. Repository Layout

```
agentic-mysql-database-application/
├── main.py                  # Entry point — starts uvicorn (FastAPI backend)
├── pyproject.toml           # Dependency manifest (uv)
├── .env                     # Local secrets — never commit
├── api/
│   ├── app.py               # FastAPI app, routes, middleware, logging
│   ├── agent.py             # AgenticWorkflow dataclass + LangGraph orchestration
│   ├── tools/
│   │   ├── __init__.py      # Minimal — points to helpers.py
│   │   └── helpers.py       # build_tools(), _get_langgraph_agent(), utility helpers
│   └── utils/
│       ├── config.py        # Env var loading, DB_CONFIG, CONTEXT_DEFAULTS
│       ├── db.py            # Connection pool, fetch_all / fetch_one / fetch_matrix
│       └── trimmer.py       # Context window trimming of LangGraph checkpoint state
└── front/
    └── app.py               # Streamlit UI — sidebar context config, chat interface
```

**Rule**: keep this layout stable. Do not add new top-level directories without a clear architectural reason.

---

## 3. Technology Stack

| Layer               | Technology                                 | Version constraint |
| ------------------- | ------------------------------------------ | ------------------ |
| Runtime             | Python                                     | ≥ 3.13             |
| Package manager     | uv                                         | latest             |
| API framework       | FastAPI + Uvicorn                          | latest stable      |
| Agent orchestration | LangGraph `create_react_agent`             | ≥ 0.3.0            |
| LLM integration     | LangChain OpenAI (`ChatOpenAI`)            | ≥ 0.3.0            |
| LLM provider        | OpenAI                                     | ≥ 1.76.0           |
| Database driver     | `mysql-connector-python` (connection pool) | ≥ 9.0.0            |
| Config              | `python-dotenv`                            | ≥ 1.0.1            |
| Frontend            | Streamlit                                  | ≥ 1.40.0           |

**Do not introduce new runtime dependencies** without updating `pyproject.toml` and documenting the reason here.

---

## 4. Environment Variables

All configuration is read from environment variables or a `.env` file (loaded by `python-dotenv` in `config.py`).

| Variable                    | Default                 | Required          |
| --------------------------- | ----------------------- | ----------------- |
| `OPENAI_API_KEY`            | —                       | **yes**           |
| `OPENAI_MODEL`              | —                       | **yes**           |
| `DB_HOST`                   | `localhost`             | no                |
| `DB_USER`                   | `root`                  | no                |
| `DB_PASSWORD`               | _(empty)_               | no                |
| `DB_NAME`                   | `cars_db`               | no                |
| `DB_PORT`                   | `3306`                  | no                |
| `PORT`                      | `8000`                  | no                |
| `BACKEND_URL`               | `http://localhost:8000` | no (Streamlit)    |
| `CTX_MAX_MESSAGES`          | `40`                    | no                |
| `CTX_MAX_RESPONSE_TOKENS`   | `1024`                  | no                |

**Never hard-code credentials or API keys.** `_resolve_api_key()` and `_resolve_model()` in `helpers.py` raise `ValueError` if the env vars are missing — preserve these guards.

---

## 5. Architecture & Key Patterns

### 5.1 Agent workflow (`api/agent.py`)

```
HTTP POST /chat  { message, session_id }
    └─▶ AgenticWorkflow.run_chat()
            ├─▶ _resolve_api_key() / _resolve_model()
            ├─▶ get_trim_settings()          ← reads max_response_tokens
            ├─▶ _get_langgraph_agent()       ← cached per (api_key, model, max_tokens)
            ├─▶ agent.get_state()            ← load prior checkpoint messages
            ├─▶ trim_messages()              ← hard-cap at max_messages
            ├─▶ agent.update_state()         ← RemoveMessage to delete surplus msgs
            └─▶ agent.invoke()               ← LangGraph ReAct loop
                    ├─▶ ChatOpenAI (temperature=0, max_tokens)
                    └─▶ @tool functions from build_tools()
                            └─▶ db.py fetch_* helpers
```

Key invariants:

- `AgenticWorkflow` is instantiated **once** as a module-level singleton in `app.py`.
- Agent instances are cached in `_agent_cache` keyed on `(api_key, model, max_tokens)`. Changing `max_response_tokens` via `/context-config` automatically creates a new agent on the next request.
- `recursion_limit = max_steps * 2` (default `8 * 2 = 16`).

### 5.2 Tool definitions (`api/tools/helpers.py` — `build_tools()`)

All tools are `@tool`-decorated functions defined inside `build_tools()`. The DB logic lives inline in each function — there is no separate handler file or dispatch router.

```python
def build_tools() -> list[Any]:
    @tool(description="One sentence the LLM uses to decide when to call this tool.")
    def my_tool(param: str) -> str:
        """Docstring."""
        rows = fetch_all("SELECT ... WHERE col = %s", (param,))
        return json.dumps({"result": rows})

    return [execute_sql, get_schema, my_tool]
```

**Critical rules for tools:**

- **Never raise an exception from inside a `@tool` function.** Exceptions bypass LangGraph's tool-result mechanism and surface directly as HTTP errors. Return an error JSON string instead:
  ```python
  return json.dumps({"error": "reason"})
  ```
- Return value must always be a JSON string (`json.dumps(...)`).
- The `@tool` description string is what the LLM reads. Make it precise and action-oriented.

### 5.3 Database layer (`api/utils/db.py`)

Three primitives — use the right one:

| Function                   | Use case                                    |
| -------------------------- | ------------------------------------------- |
| `fetch_all(query, params)` | Multiple rows → `list[dict]`                |
| `fetch_one(query, params)` | Single row → `dict` or `None`               |
| `fetch_matrix(query)`      | Column names + raw rows for tabular display |

Rules:

- Always pass user-controlled values via the **`params` tuple** — never string-interpolate them into SQL.
- The connection pool (`pool_size=10`) is a module-level singleton — do not create additional pools.
- All `fetch_*` functions auto-sanitize MySQL types (dates → ISO strings, `Decimal` → float, `bytes` → UTF-8 string). Never call `cursor.fetchall()` directly.

### 5.4 Context window trimming (`api/utils/trimmer.py`)

Before each `agent.invoke()`, the prior checkpoint state is trimmed to `max_messages`.

**Algorithm:**

1. Separate `SystemMessage`s (preserve at front).
2. If `non_system` message count exceeds the budget, find the naive cut index (`len - budget`).
3. **Advance the cut forward to the next `HumanMessage`** — this guarantees we never split an `AIMessage(tool_calls)` / `ToolMessage` pair, which causes an OpenAI 400.
4. Apply deletions via `RemoveMessage` in `agent.update_state()` — the `add_messages` reducer requires explicit `RemoveMessage` objects to delete; passing a shorter list alone does NOT delete the surplus messages.

**Settings** (tunable at runtime via `/context-config`):

| Setting               | Default | Env var                    |
| --------------------- | ------- | -------------------------- |
| `max_messages`        | `40`    | `CTX_MAX_MESSAGES`         |
| `max_response_tokens` | `1024`  | `CTX_MAX_RESPONSE_TOKENS`  |

`max_response_tokens` controls `ChatOpenAI(max_tokens=...)` at agent build time (part of the agent cache key). `max_messages` controls checkpoint trimming.

---

## 6. Adding a New Tool

All new tools go inside `build_tools()` in `api/tools/helpers.py`.

### Step 1 — Write the `@tool` function

```python
@tool(description="One-sentence description the LLM uses to select this tool.")
def my_new_tool(param_name: str) -> str:
    """Brief docstring."""
    if not param_name.strip():
        return json.dumps({"error": "param_name is required"})
    rows = fetch_all("SELECT ... WHERE name = %s", (param_name,))
    return json.dumps({"result": rows})
```

### Step 2 — Add to the returned list

```python
return [execute_sql, get_schema, my_new_tool]
```

### Step 3 — Update the system prompt in `agent.py`

Add a bullet under "Your capabilities" so the LLM knows the tool exists and when to use it.

---

## 7. FastAPI Routes (`api/app.py`)

| Method | Path              | Purpose                                            |
| ------ | ----------------- | -------------------------------------------------- |
| `POST` | `/ping`           | Test DB connection, return table list              |
| `POST` | `/chat`           | Full agent chat with LangGraph orchestration       |
| `GET`  | `/context-config` | Read current context settings                      |
| `POST` | `/context-config` | Update `max_messages` and/or `max_response_tokens` |

**Error handling**: all exceptions in `/chat` are caught and logged with `traceback.format_exc()` via the `autobase` logger before being wrapped as `HTTPException`. Check the backend terminal for the full traceback when debugging.

**`POST /chat`** request / response:

```json
// request
{ "message": "How many rows are in the orders table?", "session_id": "uuid" }

// response
{
  "reply": "There are 1,042 rows...",
  "tool_calls": [{ "name": "execute_sql", "args": {}, "result": {} }],
  "session_id": "uuid"
}
```

**`POST /context-config`** — omit fields you don't want to change:

```json
{ "max_messages": 20, "max_response_tokens": 512 }
```

---

## 8. Frontend (`front/app.py` — Streamlit)

The frontend is a Streamlit app. It communicates with the FastAPI backend **exclusively via HTTP** (`requests` library) — it never imports from `api/`.

### Structure

- **Sidebar** — Context Window tuning:
  - `Max response tokens` slider (64–8192, step 64) → `max_response_tokens`
  - `Max messages` slider (4–200) → `max_messages`
  - **Apply** button → `POST /context-config`
  - Settings loaded on startup from `GET /context-config` via `@st.cache_data`
- **Main area** — Chat interface:
  - `st.chat_message` bubbles for user and assistant turns
  - `st.expander` blocks per tool call showing args + result JSON
  - `st.chat_input` triggers `POST /chat`

### Key patterns

- Conversation history → `st.session_state.messages` (list of `{role, content, tool_calls}`)
- `session_id` → `st.session_state.session_id` (sent with every `/chat` request for LangGraph thread continuity)
- Backend URL → `BACKEND_URL` env var (default `http://localhost:8000`)
- Connection errors → inline `⚠️` warning message (no crash, no demo mode)

---

## 9. System Prompt (`SYSTEM_PROMPT` in `agent.py`)

When modifying the system prompt:

- Keep the capabilities bullet list **in sync** with the tools returned by `build_tools()`.
- Do not list capabilities without a corresponding tool — the LLM will hallucinate calls.
- `temperature=0` is intentional: determinism is preferred for SQL generation.

---

## 10. Security Requirements

- **Parameterised queries only** for all user-supplied SQL values — never string-interpolate.
- The `execute_sql` SELECT guard (`^SELECT\s` regex) is a hard constraint — never remove it. Non-SELECT input returns an error JSON, not an exception.
- `OPENAI_API_KEY` is read server-side only; never echoed in responses or sent to the frontend.
- All route payloads use Pydantic `BaseModel` — do not accept raw `dict` bodies.
- `allow_origins=["*"]` is acceptable for local development; lock it down for production.

---

## 11. Code Style

- `from __future__ import annotations` at the top of every Python file.
- Type annotations on all public function signatures.
- `dataclass` for stateful classes (`AgenticWorkflow`); plain functions for stateless utilities.
- Private helpers prefixed with `_` (e.g. `_get_langgraph_agent`, `_resolve_api_key`).
- No f-string SQL with user input — always use parameterised queries.

---

## 12. Running Locally

```bash
# 1. Install dependencies
uv sync

# 2. Create .env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=yourpassword
DB_NAME=your_database

# 3. Start the FastAPI backend  (terminal 1)
uv run python main.py
# → http://localhost:8000

# 4. Start the Streamlit frontend  (terminal 2)
uv run streamlit run front/app.py
# → http://localhost:8501
```

`reload=True` is set in `main.py` — the backend hot-reloads on file changes.

---

## 13. Common Pitfalls

| Pitfall | Correct approach |
| ------- | ---------------- |
| Raising an exception inside a `@tool` function | Return `json.dumps({"error": "reason"})` — exceptions escape LangGraph |
| Calling `agent.update_state(config, {"messages": shorter_list})` to trim | Use `RemoveMessage` objects — the `add_messages` reducer upserts, never deletes |
| Cutting the message list mid-tool-call sequence | `trim_messages()` always advances cut to a `HumanMessage` boundary |
| Creating a new `ChatOpenAI` per request | Agent is cached by `(api_key, model, max_tokens)`; only recreated when settings change |
| Adding a tool without updating the system prompt | The LLM won't know the tool exists |
| Importing from `api/` in `front/app.py` | Frontend must stay isolated — HTTP only |
| Calling `cursor.fetchall()` directly | Always go through `fetch_all` / `fetch_matrix` for type sanitization |
| Using `recursion_limit` to silence a looping agent | Fix the tool or prompt; don't raise the limit |


---

## 1. Project Overview

AutoBase is a **Python-first agentic MySQL application**. It connects to a MySQL database and exposes a conversational agent that translates natural language into SQL and explores the schema.

### Core philosophy

- The **backend owns all intelligence**: OpenAI calls, tool execution, and agent state live exclusively in Python. The frontend is a thin HTTP client.
- **Tool-first design**: every capability the agent has is expressed as a `@tool`-decorated function in `build_tools()`. No freeform logic leaks outside tools.
- **SELECT-only safety**: the SQL execution guard is a hard constraint. Never relax it without an explicit security review.
- **Stateful sessions**: conversation memory is persisted per `session_id` via LangGraph's `InMemorySaver` checkpointer. Never bypass this with stateless calls.
- **Frontend isolation**: the Streamlit frontend communicates with the FastAPI backend exclusively via HTTP. It never imports from the `api/` package.

---

## 2. Repository Layout

```
agentic-mysql-database-application/
├── main.py                  # Entry point — starts uvicorn (FastAPI backend)
├── pyproject.toml           # Dependency manifest
├── api/
│   ├── app.py               # FastAPI app, routes, middleware
│   ├── agent.py             # AgenticWorkflow dataclass + LangGraph orchestration
│   ├── tools/
│   │   ├── __init__.py      # Minimal — points to helpers.py
│   │   └── helpers.py       # build_tools() — all @tool definitions with inline DB logic
│   └── utils/
│       ├── config.py        # Env var loading, DB_CONFIG, context window defaults
│       ├── db.py            # Connection pool, fetch_all / fetch_one / fetch_matrix
│       └── trimmer.py       # Context window trimming of LangGraph state messages
└── front/
    └── app.py               # Streamlit UI — sidebar context config, chat interface
```

**Rule**: keep this layout stable. Do not add new top-level directories without a clear architectural reason.

---

## 3. Technology Stack

| Layer               | Technology                                 | Version constraint |
| ------------------- | ------------------------------------------ | ------------------ |
| Runtime             | Python                                     | ≥ 3.13             |
| API framework       | FastAPI + Uvicorn                          | latest stable      |
| Agent orchestration | LangGraph `create_react_agent`             | ≥ 0.3.0            |
| LLM integration     | LangChain OpenAI (`ChatOpenAI`)            | ≥ 0.3.0            |
| LLM provider        | OpenAI (`gpt-4o` default)                  | ≥ 1.76.0           |
| Database driver     | `mysql-connector-python` (connection pool) | ≥ 9.0.0            |
| Config              | `python-dotenv`                            | ≥ 1.0.1            |
| Frontend            | Streamlit                                  | ≥ 1.40.0           |

**Do not introduce new runtime dependencies** without updating `pyproject.toml` and documenting the reason here.
