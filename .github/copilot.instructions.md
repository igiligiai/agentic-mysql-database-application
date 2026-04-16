# AutoBase — Copilot Development Instructions

Comprehensive guidelines for developing, extending, and improving the AutoBase agentic MySQL intelligence application. Read this file before making any changes.

---

## 1. Project Overview

AutoBase is a **Python-first agentic data intelligence application**. It connects to a MySQL database and exposes a conversational agent that translates natural language into SQL, explores schema, traces table lineage, and runs data quality checks.

### Core philosophy

- The **backend owns all intelligence**: OpenAI calls, tool dispatch, and agent state live exclusively in Python. The frontend is a thin UI shell.
- **Tool-first design**: every capability the agent has is expressed as a named, schema-validated tool. No freeform logic leaks outside tools.
- **SELECT-only safety**: the SQL execution guard is a hard constraint. Never relax it without an explicit security review.
- **Stateful sessions**: conversation memory is persisted per `session_id` via LangGraph's `InMemorySaver` checkpointer. Never bypass this with stateless calls.

---

## 2. Repository Layout

```
agentic-mysql-database-application/
├── main.py                  # Entry point — starts uvicorn
├── pyproject.toml           # Dependency manifest
├── api/
│   ├── app.py               # FastAPI app, routes, middleware
│   ├── agent.py             # AgenticWorkflow dataclass + LangGraph orchestration
│   ├── tools/
│   │   ├── __init__.py      # Public re-exports of all tool functions + dispatch_tool
│   │   ├── handlers.py      # One function per tool (execute_sql_tool, etc.) + dispatch_tool()
│   │   └── helpers.py       # Pure utilities: DB helpers, LangGraph wiring, LLM factory
│   └── utils/
│       ├── config.py        # Env var loading, DB_CONFIG, path constants
│       ├── db.py            # Connection pool, fetch_all / fetch_one / fetch_matrix
│       └── tools.json       # OpenAI function-calling schema (source of truth for tool contracts)
└── front/
    ├── index.html           # App shell, tabs, sidebar
    ├── styles.css           # All visual styles
    └── app.js               # UI state machine, API calls, D3 lineage graph, demo mode
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
| Frontend            | Vanilla JS + D3 v7                         | no bundler         |
| Fonts               | Google Fonts (DM Mono, Syne)               | CDN                |

**Do not introduce new runtime dependencies** without updating `pyproject.toml` and documenting the reason here.

---

## 4. Environment Variables

All configuration is read from environment variables (or a `.env` file loaded by `python-dotenv` in `api/utils/config.py`).

| Variable         | Default     | Required |
| ---------------- | ----------- | -------- |
| `OPENAI_API_KEY` | —           | **yes**  |
| `DB_HOST`        | `localhost` | no       |
| `DB_USER`        | `root`      | no       |
| `DB_PASSWORD`    | _(empty)_   | no       |
| `DB_NAME`        | `cars_db`   | no       |
| `DB_PORT`        | `3306`      | no       |
| `PORT`           | `8000`      | no       |

**Never hard-code credentials or API keys.** `_resolve_api_key()` in `helpers.py` raises a `ValueError` if `OPENAI_API_KEY` is missing — preserve this guard.

---

## 5. Architecture Patterns

### 5.1 Agent workflow (`api/agent.py`)

```
User message
    └─▶ AgenticWorkflow.run_chat()
            └─▶ _get_langgraph_agent()      ← cached per (api_key, model)
                    └─▶ LangGraph ReAct agent
                            ├─▶ ChatOpenAI (gpt-4o, temperature=0)
                            └─▶ LangGraph tool nodes (one per tool)
                                    └─▶ AgenticWorkflow.run_tool()
                                            └─▶ dispatch_tool()
                                                    └─▶ <tool>_tool() in handlers.py
                                                            └─▶ db.py fetch_* functions
```

Key invariants:

- `AgenticWorkflow` is instantiated **once** as a module-level singleton in `app.py` (`agent = AgenticWorkflow()`).
- Agent instances are cached in `_agent_cache` keyed by `(api_key, model)`. Do not recreate agents per request.
- `recursion_limit` is set to `max_steps * 2`. Default `max_steps=8`. Adjust only if a new tool chain genuinely requires deeper nesting.
- The `history` list on `AgenticWorkflow` records every tool call for the lifetime of the process. This is for debugging; do not use it as a source of truth for session context (LangGraph checkpointer owns that).

### 5.2 Tool schema contract (`api/utils/tools.json`)

`tools.json` is the **single source of truth** for every tool's OpenAI function-calling schema. It drives:

1. Tool descriptions surfaced to the LLM (via `_tool_description()` in `helpers.py`).
2. Parameter documentation visible in OpenAI's function-calling API.

When adding or modifying a tool, update `tools.json` **first**, then implement the handler. The JSON schema must match the Python function signature exactly.

### 5.3 Tool dispatch (`api/tools/handlers.py`)

`dispatch_tool(tool, args)` is the single entry point for all tool invocations. It maps tool name strings to typed handler functions. Pattern:

```python
if tool == "my_new_tool":
    return my_new_tool_tool(payload)
```

Each handler function:

- Takes `args: dict[str, Any]` (already validated upstream by LangGraph/Pydantic).
- Returns `dict[str, Any]` — the raw result serialised as JSON by the caller.
- Raises `HTTPException` for validation errors (bad input) or `mysql.connector.Error` for DB failures (bubbles up to FastAPI exception handlers).
- Never returns raw exception objects or Python tracebacks in the result payload.

### 5.4 Database layer (`api/utils/db.py`)

Three primitives — use the right one:

| Function                   | Use case                                    |
| -------------------------- | ------------------------------------------- |
| `fetch_all(query, params)` | Multiple rows, returns `list[dict]`         |
| `fetch_one(query, params)` | Single row or `None`                        |
| `fetch_matrix(query)`      | Column names + raw rows for tabular display |

Rules:

- Always pass user-controlled values as **parameterised queries** (`params` tuple), never by string interpolation.
- Table and column names cannot be parameterised in MySQL — use `validate_identifier()` from `helpers.py` to whitelist and backtick-escape them before interpolating into SQL strings.
- The connection pool (`pool_size=10`) is a module-level singleton. Do not create additional pools.

### 5.5 Layer inference (`infer_layer` in `helpers.py`)

Tables are classified into `bronze / silver / gold` layers based on name patterns:

| Pattern                                                            | Layer            |
| ------------------------------------------------------------------ | ---------------- |
| contains `bronze`, `raw`, `landing`, or starts with `stg_`         | bronze           |
| contains `silver`, `_clean`, `_typed`                              | silver           |
| contains `gold`, `vw_`, `_summary`, `_report`, or starts with `vw` | gold             |
| everything else                                                    | silver (default) |

When the database schema uses different naming conventions, extend the conditionals in `infer_layer()` — do not add a new function.

---

## 6. Adding a New Tool

Follow these steps in order. Do not skip steps.

### Step 1 — Define the schema in `tools.json`

```json
{
  "type": "function",
  "function": {
    "name": "my_new_tool",
    "description": "One-sentence description used by the LLM to decide when to call this tool.",
    "parameters": {
      "type": "object",
      "properties": {
        "param_name": {
          "type": "string",
          "description": "Clear description of what this parameter controls."
        }
      },
      "required": ["param_name"]
    }
  }
}
```

### Step 2 — Implement the handler in `handlers.py`

```python
def my_new_tool_tool(args: dict[str, Any]) -> dict[str, Any]:
    param = str(args.get("param_name") or "").strip()
    if not param:
        raise HTTPException(status_code=400, detail="param_name is required")
    # ... implementation using fetch_all / fetch_one / fetch_matrix
    return {"result": ...}
```

### Step 3 — Register in `dispatch_tool()` (`handlers.py`)

```python
if tool == "my_new_tool":
    return my_new_tool_tool(payload)
```

### Step 4 — Export from `__init__.py`

```python
from .handlers import my_new_tool_tool
__all__ = [..., "my_new_tool_tool"]
```

### Step 5 — Wire the LangGraph tool in `helpers.py`

Inside `_build_langgraph_tools()`, add:

```python
@tool(description=_tool_description(tool_schema_by_name, "my_new_tool", "Fallback description."))
def my_new_tool(param_name: str) -> str:
    """One-line docstring."""
    result = run_tool_fn("my_new_tool", {"param_name": param_name})
    return json.dumps(result)
```

Then add `my_new_tool` to the returned list.

### Step 6 — Add a demo handler in `front/app.js`

Inside `executeToolDemo()`, add a `if (name === 'my_new_tool')` branch that returns realistic-looking static data matching the live tool's response shape.

---

## 7. FastAPI Routes

| Method | Path    | Purpose                                             |
| ------ | ------- | --------------------------------------------------- |
| `GET`  | `/`     | Serve `front/index.html`                            |
| `POST` | `/ping` | Connect + discover tables from `information_schema` |
| `POST` | `/tool` | Direct single-tool dispatch (bypasses agent LLM)    |
| `POST` | `/chat` | Full agent chat with LangGraph orchestration        |

### Request / response shapes

**`POST /tool`**

```json
{
  "tool": "execute_sql",
  "args": { "query": "SELECT ...", "explanation": "..." }
}
```

**`POST /chat`**

```json
{ "message": "...", "session_id": "uuid", "model": "gpt-4o" }
```

Response:

```json
{
  "reply": "...",
  "tool_calls": [{ "name": "...", "args": {}, "result": {} }],
  "session_id": "..."
}
```

**Never add authenticated routes without also adding proper auth middleware** (Bearer token or API key header). The current CORS policy (`allow_origins=["*"]`) is acceptable for local dev but must be locked down for any public deployment.

---

## 8. Frontend Conventions (`front/`)

The frontend is deliberately framework-free. Keep it that way.

### State machine

All mutable UI state lives in the `state` object at the top of `app.js`:

```js
const state = {
  connected,
  backendUrl,
  sessionId,
  schema,
  tableList,
  lineageData,
  demoMode,
  isThinking,
  lineageSim,
  lineageZoom,
};
```

Do not add global variables outside `state`. New flags go inside this object.

### API calls

All backend communication goes through `callBackend(endpoint, body)`. Never call `fetch()` directly elsewhere. This ensures uniform error handling and uses `state.backendUrl`.

### Demo mode

Demo mode (`state.demoMode = true`) activates when the backend is unreachable. All tool calls are routed to `executeToolDemo()` instead of the real backend. When adding a new tool:

- Add a realistic demo response in `executeToolDemo()`.
- Add corresponding entries to `DEMO_SCHEMA` or `DEMO_LINEAGE` if the new tool reveals structural data.

### D3 lineage graph

The lineage graph uses D3 force simulation. Nodes are coloured by layer:

- `bronze` → amber
- `silver` → steel blue
- `gold` → gold/yellow
- Node shape: TABLE = rectangle, VIEW = diamond (use CSS class `node-view`).

Preserve the zoom/pan behaviour (`d3.zoom()` stored in `state.lineageZoom`). Do not replace D3 with another library.

### Tab system

Tabs are managed by `switchTab(name)`. Tab IDs are `chat`, `schema`, `lineage`. Panel IDs follow the pattern `panel-<name>`. Add new tabs by extending both the `.tabs` div in HTML and the `switchTab` switch statement.

---

## 9. System Prompt (`SYSTEM_PROMPT` in `agent.py`)

The system prompt defines the agent's persona ("AutoBase") and capabilities. When modifying it:

- Keep the bullet list of capabilities in sync with the actual tools available.
- Do not add tool descriptions that don't exist — the LLM will hallucinate calls.
- The prompt references "Car data warehouse (bronze/silver/gold layers)". Update the database description if the schema changes significantly.
- `temperature=0` is intentional: determinism is preferred over creativity for SQL generation.

---

## 10. Security Requirements

### SQL injection prevention

- **Parameterised queries only** for all user-supplied values passed as data.
- **`validate_identifier()`** for all user-supplied table/column names that must be interpolated. This function raises `HTTPException(400)` if the name contains anything other than `[A-Za-z_][A-Za-z0-9_]*`.
- The `execute_sql_tool` regex guard (`^SELECT\s`) must remain. Never allow DDL or DML through this tool.

### API key handling

- `OPENAI_API_KEY` is read server-side only. It is never sent to the frontend or echoed in responses.
- `_resolve_api_key()` is the single place that reads the key. Do not inline `os.getenv("OPENAI_API_KEY")` elsewhere.

### Input validation

- All route payloads use Pydantic `BaseModel`. Do not accept raw `dict` bodies on routes.
- `message` is validated non-empty in `/chat` before calling the agent.

### CORS

- Current `allow_origins=["*"]` is suitable for local development only. For production, restrict to the actual frontend origin.

---

## 11. Code Style

### Python

- `from __future__ import annotations` at the top of every Python file.
- Type annotations on all public function signatures.
- `dataclass` for stateful classes (`AgenticWorkflow`); plain functions for stateless utilities.
- Use `Any` from `typing` for heterogeneous dicts; prefer typed `TypedDict` or `dataclass` when the shape is fixed and known.
- Private helpers prefixed with `_` (e.g., `_build_langgraph_tools`, `_resolve_api_key`).
- No f-string SQL construction with user input — always use parameterised queries.

### JavaScript

- Vanilla ES2020+. No transpiler, no bundler, no framework.
- `const` by default; `let` only when reassignment is needed.
- Async/await for all I/O. Never mix `.then()` chains and `await` in the same function.
- DOM queries via `document.getElementById()` — avoid `querySelector` with complex selectors.
- Use `sleep(ms)` (defined in `app.js`) for demo mode artificial delays.

---

## 12. Testing Approach

There are no automated tests yet. When adding them:

### Unit tests (Python)

- Use `pytest`.
- Test each handler function in `handlers.py` in isolation by mocking `fetch_all`, `fetch_one`, `fetch_matrix` with `unittest.mock.patch`.
- Test `validate_identifier()` with valid names, SQL-injection attempts, and empty strings.
- Test `infer_layer()` with table names covering all layer patterns.
- Test `_message_to_text()` with string, list-of-dicts, and None inputs.

### Integration tests

- Use `pytest` + `httpx.AsyncClient` (ASGI transport) against a test MySQL instance.
- Test `/ping`, `/tool`, and `/chat` endpoints.
- Never test against the production database.

### Frontend tests

- Vanilla JS unit tests with Vitest or plain `<script type="module">` test files.
- Test `state` transitions, `executeToolDemo()` responses, and D3 data-binding helpers.

---

## 13. Running Locally

```bash
# 1. Install dependencies
pip install -e .

# 2. Set environment variables (or create .env)
export OPENAI_API_KEY=sk-...
export DB_NAME=cars_db
export DB_PASSWORD=...

# 3. Start the server
python main.py
# → http://localhost:8000
```

`reload=True` is active in development (set in `main.py`). The server hot-reloads on Python file changes.

---

## 14. Extending the Data Model

The application is built around a **bronze / silver / gold** medallion architecture:

| Layer  | Purpose                                | Naming pattern                            |
| ------ | -------------------------------------- | ----------------------------------------- |
| Bronze | Raw ingest, no transformations         | `*_bronze`, `raw_*`, `landing_*`, `stg_*` |
| Silver | Cleaned, typed, deduplicated           | `*_silver`, `*_clean`, `*_typed`          |
| Gold   | Aggregated, joined, report-ready views | `vw_*`, `*_summary`, `*_report`, `*_gold` |

When adding new tables or views to the MySQL database, follow these naming conventions so `infer_layer()` classifies them correctly without code changes.

---

## 15. Common Pitfalls

| Pitfall                                             | Correct approach                                                                |
| --------------------------------------------------- | ------------------------------------------------------------------------------- |
| Calling `client.chat.completions` directly          | Use `agent.run_chat()` — LangGraph manages the LLM call                         |
| Creating a new `ChatOpenAI` per request             | Agent is cached in `_agent_cache`; reuse it                                     |
| String-interpolating table names directly           | Always call `validate_identifier()` first                                       |
| Adding business logic to `app.py` routes            | Routes call `agent.run_tool()` or `agent.run_chat()`; logic belongs in handlers |
| Returning Python exceptions in tool results         | Raise `HTTPException`; let FastAPI handle serialisation                         |
| Modifying `tools.json` without updating the handler | Both must stay in sync                                                          |
| Frontend calling OpenAI directly                    | All LLM calls stay in the Python backend                                        |
| Using `recursion_limit` to silence runaway agents   | Fix the tool logic; don't raise the limit                                       |
