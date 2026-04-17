# AutoBase — Copilot Development Instructions

Comprehensive guidelines for developing, extending, and improving the AutoBase agentic MySQL application. Read this file before making any changes.

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

---

## 4. Environment Variables

All configuration is read from environment variables (or a `.env` file loaded by `python-dotenv` in `api/utils/config.py`).

| Variable                    | Default     | Required |
| --------------------------- | ----------- | -------- |
| `OPENAI_API_KEY`            | —           | **yes**  |
| `OPENAI_MODEL`              | —           | **yes**  |
| `DB_HOST`                   | `localhost` | no       |
| `DB_USER`                   | `root`      | no       |
| `DB_PASSWORD`               | _(empty)_   | no       |
| `DB_NAME`                   | `cars_db`   | no       |
| `DB_PORT`                   | `3306`      | no       |
| `PORT`                      | `8000`      | no       |
| `BACKEND_URL`               | `http://localhost:8000` | no (Streamlit) |
| `CTX_MAX_MESSAGES`          | `40`        | no       |
| `CTX_MAX_TOOL_RESULT_CHARS` | `4000`      | no       |
| `CTX_KEEP_LAST_N_PAIRS`     | `10`        | no       |
| `CTX_TRIM_ENABLED`          | `true`      | no       |

The `CTX_*` variables control the context window trimmer (see §5.4). They set defaults that can be overridden at runtime via the `/context-config` endpoint.

**Never hard-code credentials or API keys.** `_resolve_api_key()` in `helpers.py` raises a `ValueError` if `OPENAI_API_KEY` is missing — preserve this guard.

---

## 5. Architecture Patterns

### 5.1 Agent workflow (`api/agent.py`)

```
User message (HTTP POST /chat)
    └─▶ AgenticWorkflow.run_chat()
            ├─▶ trim_messages()          ← trims prior state before invocation (§5.4)
            └─▶ _get_langgraph_agent()   ← cached per (api_key, model)
                    └─▶ LangGraph ReAct agent
                            ├─▶ ChatOpenAI (temperature=0)
                            └─▶ @tool functions from build_tools()
                                    └─▶ db.py fetch_* (with _sanitize)
```

Key invariants:

- `AgenticWorkflow` is instantiated **once** as a module-level singleton in `app.py` (`agent = AgenticWorkflow()`).
- Agent instances are cached in `_agent_cache` keyed by `(api_key, model)`. Do not recreate agents per request.
- `recursion_limit` is set to `max_steps * 2`. Default `max_steps=8`.

### 5.2 Tool definitions (`api/tools/helpers.py`)

All tools are defined as `@tool`-decorated functions inside `build_tools()`. Each tool contains its own DB logic inline — there is no separate handler layer or dispatch function.

```python
def build_tools() -> list[Any]:
    @tool(description="One-sentence description used by the LLM.")
    def my_tool(param: str) -> str:
        """Docstring."""
        # DB logic here using fetch_all / fetch_matrix
        return json.dumps(result)

    return [my_tool, ...]
```

Rules:
- The `@tool` description is what the LLM sees when deciding which tool to call. Make it precise.
- Return value must be a JSON string (`json.dumps(...)`).
- Raise `HTTPException` for validation errors — never return error dicts.
- Keep all DB logic inside the tool function. No separate helper modules for tool implementations.

### 5.3 Database layer (`api/utils/db.py`)

Three primitives — use the right one:

| Function                   | Use case                                    |
| -------------------------- | ------------------------------------------- |
| `fetch_all(query, params)` | Multiple rows, returns `list[dict]`         |
| `fetch_one(query, params)` | Single row or `None`                        |
| `fetch_matrix(query)`      | Column names + raw rows for tabular display |

Rules:

- Always pass user-controlled values as **parameterised queries** (`params` tuple), never by string interpolation.
- The connection pool (`pool_size=10`) is a module-level singleton. Do not create additional pools.

#### JSON sanitization

All `fetch_*` functions automatically sanitize MySQL-specific Python types before returning:

| MySQL Python type                    | Converted to                        |
| ------------------------------------ | ----------------------------------- |
| `datetime.date`, `datetime.datetime` | ISO 8601 string (`str.isoformat()`) |
| `datetime.timedelta`                 | `str()`                             |
| `Decimal`                            | `float`                             |
| `bytes` / `bytearray`                | UTF-8 decoded `str`                 |

Do not bypass sanitization by calling `cursor.fetchall()` directly — always go through the `fetch_*` functions.

### 5.4 Context window trimming (`api/utils/trimmer.py`)

Before each `agent.invoke()`, the prior LangGraph state is trimmed to control the LLM's context window.

**Trimming strategy** (applied in order):

1. **System message preservation** — system messages are always kept at the front.
2. **Tool result truncation** — `ToolMessage` content exceeding `max_tool_result_chars` is truncated.
3. **Turn-based windowing** — only the last `keep_last_n_pairs` user/AI turns are retained.
4. **Hard message cap** — total messages capped at `max_messages`.
5. **Orphan cleanup** — leading `ToolMessage`s without a preceding `AIMessage` are removed.

**Configuration defaults:**

| Setting                 | Default | Description                                        |
| ----------------------- | ------- | -------------------------------------------------- |
| `max_messages`          | `40`    | Hard cap on total messages sent to the LLM         |
| `max_tool_result_chars` | `4000`  | Truncation threshold for individual tool results   |
| `keep_last_n_pairs`     | `10`    | Number of recent user/AI turns to preserve         |
| `trim_enabled`          | `true`  | Master toggle                                      |

**Runtime tuning**: settings can be changed via `GET /context-config` and `POST /context-config`. The Streamlit sidebar exposes these controls.

**Key invariants**:

- `trim_messages()` is a **pure function** — it does not mutate the input.
- All context window control goes through `trimmer.py`. Do not add trimming logic elsewhere.

---

## 6. Adding a New Tool

All new tools go into `build_tools()` in `api/tools/helpers.py`. There is no separate handler file or schema file.

### Step 1 — Add a `@tool` function inside `build_tools()`

```python
@tool(description="One-sentence description the LLM uses to select this tool.")
def my_new_tool(param_name: str) -> str:
    """Brief docstring."""
    if not param_name.strip():
        raise HTTPException(status_code=400, detail="param_name is required")
    rows = fetch_all("SELECT ... WHERE name = %s", (param_name,))
    return json.dumps({"result": rows})
```

### Step 2 — Add to the returned list

```python
return [execute_sql, get_schema, my_new_tool]
```

### Step 3 — Update the system prompt in `agent.py`

Add a bullet under "Your capabilities" so the LLM knows the tool exists.

---

## 7. FastAPI Routes

| Method | Path              | Purpose                                             |
| ------ | ----------------- | --------------------------------------------------- |
| `POST` | `/ping`           | Connect + discover tables from `information_schema` |
| `POST` | `/chat`           | Full agent chat with LangGraph orchestration        |
| `GET`  | `/context-config` | Read current context window trimming settings       |
| `POST` | `/context-config` | Update context window trimming settings at runtime  |

### Request / response shapes

**`POST /chat`**

```json
{ "message": "How many rows are in the orders table?", "session_id": "uuid" }
```

Response:

```json
{
  "reply": "There are 1,042 rows in the orders table.",
  "tool_calls": [{ "name": "execute_sql", "args": {}, "result": {} }],
  "session_id": "uuid"
}
```

**`GET /context-config`**

```json
{
  "max_messages": 40,
  "max_tool_result_chars": 4000,
  "keep_last_n_pairs": 10,
  "keep_system": true,
  "trim_enabled": true
}
```

**`POST /context-config`** — only include fields to change; omitted fields keep their current values.

```json
{ "max_messages": 20, "keep_last_n_pairs": 5 }
```

**Never add authenticated routes without also adding proper auth middleware.** The current CORS policy (`allow_origins=["*"]`) is acceptable for local dev but must be locked down for any public deployment.

---

## 8. Frontend (`front/app.py` — Streamlit)

The frontend is a Streamlit app that communicates with the FastAPI backend **exclusively via HTTP**. It never imports from `api/`.

### Structure

- **Sidebar** — Context Window tuning: sliders for `max_messages`, `keep_last_n_pairs`, `max_tool_result_chars`, and a `trim_enabled` checkbox. An Apply button calls `POST /context-config`.
- **Main area** — Chat interface: `st.chat_message` bubbles for user and assistant turns. Tool calls rendered as `st.expander` blocks showing args and result JSON.

### Key patterns

- All conversation history is stored in `st.session_state.messages`.
- `session_id` is stored in `st.session_state.session_id` and sent with every `/chat` request to maintain LangGraph thread continuity.
- Backend URL defaults to `http://localhost:8000` and can be overridden via the `BACKEND_URL` environment variable.
- Connection errors surface as an inline warning message — no crash, no demo mode.

---

## 9. System Prompt (`SYSTEM_PROMPT` in `agent.py`)

When modifying the system prompt:

- Keep the capabilities bullet list in sync with the tools returned by `build_tools()`.
- Do not list capabilities that don't have a corresponding tool — the LLM will hallucinate calls.
- `temperature=0` is intentional: determinism is preferred for SQL generation.

---

## 10. Security Requirements

### SQL injection prevention

- **Parameterised queries only** for all user-supplied values passed as data.
- The `execute_sql` tool regex guard (`^SELECT\s`) must remain. Never allow DDL or DML through this tool.

### API key handling

- `OPENAI_API_KEY` is read server-side only. It is never sent to the frontend or echoed in responses.
- `_resolve_api_key()` is the single place that reads the key.

### Input validation

- All route payloads use Pydantic `BaseModel`. Do not accept raw `dict` bodies on routes.
- `message` is validated non-empty in `/chat` before calling the agent.

---

## 11. Code Style

### Python

- `from __future__ import annotations` at the top of every Python file.
- Type annotations on all public function signatures.
- `dataclass` for stateful classes (`AgenticWorkflow`); plain functions for stateless utilities.
- Private helpers prefixed with `_` (e.g., `_get_langgraph_agent`, `_resolve_api_key`).
- No f-string SQL construction with user input — always use parameterised queries.

---

## 12. Testing Approach

There are no automated tests yet. When adding them:

### Unit tests (Python)

- Use `pytest`.
- Test each `@tool` function in `build_tools()` by mocking `fetch_all` and `fetch_matrix` with `unittest.mock.patch`.
- Test `_message_to_text()` with string, list-of-dicts, and None inputs.
- Test the SELECT guard in `execute_sql` with non-SELECT inputs.

### Integration tests

- Use `pytest` + `httpx.AsyncClient` (ASGI transport) against a test MySQL instance.
- Test `/ping`, `/chat`, and `/context-config` endpoints.
- Never test against the production database.

---

## 13. Running Locally

```bash
# 1. Install dependencies
uv sync

# 2. Set environment variables (or create .env)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
DB_NAME=your_database
DB_PASSWORD=...

# 3. Start the FastAPI backend
uv run python main.py
# → http://localhost:8000

# 4. Start the Streamlit frontend (separate terminal)
uv run streamlit run front/app.py
# → http://localhost:8501
```

`reload=True` is active in development. The backend hot-reloads on Python file changes.

---

## 14. Common Pitfalls

| Pitfall                                          | Correct approach                                                              |
| ------------------------------------------------ | ----------------------------------------------------------------------------- |
| Calling `client.chat.completions` directly       | Use `agent.run_chat()` — LangGraph manages the LLM call                       |
| Creating a new `ChatOpenAI` per request          | Agent is cached in `_agent_cache`; reuse it                                   |
| Adding business logic to `app.py` routes         | Routes call `agent.run_chat()`; logic belongs in tool functions               |
| Returning Python exceptions in tool results      | Raise `HTTPException`; let FastAPI handle serialisation                       |
| Returning `date`/`Decimal` from DB unsanitized   | All `fetch_*` functions auto-sanitize; don't bypass with raw cursor calls     |
| Adding context trimming logic outside trimmer.py | All trimming goes through `trim_messages()` in `trimmer.py`                   |
| Importing from `api/` in `front/app.py`          | Frontend is isolated — communicate only via HTTP to the FastAPI backend       |
| Frontend calling OpenAI directly                 | All LLM calls stay in the Python backend                                      |
| Using `recursion_limit` to silence runaway agents| Fix the tool logic; don't raise the limit                                     |
| Adding a tool without updating the system prompt | The LLM won't know the tool exists — always keep capabilities list in sync    |
