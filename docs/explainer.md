# AutoBase — Comprehensive Project Explanation

---

## 1. What This Project Is

**AutoBase** is a full-stack, agentic data intelligence application. Its purpose is to let users have a natural-language conversation with a MySQL database — asking questions like "show me the top 5 most driven cars" and getting back real SQL results, rendered tables, lineage graphs, and intelligent explanations. The entire intelligence loop runs on the **Python backend**; the browser is a passive display layer.

---

## 2. Repository Layout

```
agentic-mysql-database-application/
├── main.py                  # Entrypoint: starts uvicorn
├── pyproject.toml           # Python deps (FastAPI, LangChain, LangGraph, OpenAI, mysql-connector)
├── api/
│   ├── app.py               # FastAPI app — HTTP routes
│   ├── agent.py             # AgenticWorkflow — the brain
│   ├── tools/
│   │   ├── __init__.py      # Public exports
│   │   ├── handlers.py      # All 5 tool implementations (real SQL logic)
│   │   └── helpers.py       # LangChain @tool wrappers, agent factory, utilities
│   └── utils/
│       ├── config.py        # Env vars, DB config, context defaults
│       ├── db.py            # MySQL connection pool, fetch helpers
│       ├── tools.json       # OpenAI-style JSON schemas for all 5 tools
│       └── trimmer.py       # Context window trimming logic
└── front/
    ├── index.html           # Single-page app shell
    ├── styles.css           # Dark-theme styles
    └── app.js               # All frontend logic (chat UI, D3 lineage, schema explorer)
```

---

## 3. Startup Path

`main.py` reads the `PORT` env var (default `8000`) and calls:
```python
uvicorn.run("api.app:app", host="0.0.0.0", port=port, reload=True)
```

Uvicorn imports `api/app.py`. At **module load time**, two things happen immediately:
1. FastAPI mounts the `front/` directory at `/front` as a static file server.
2. **`agent = AgenticWorkflow()`** is instantiated. This triggers `__post_init__`, which calls `_build_langgraph_tools()` — building all 5 LangChain tool objects and storing them in `self._langgraph_tools`. The agent cache (`_agent_cache`) is empty at this point; the actual LLM + LangGraph agent isn't built until the **first `/chat` request**.

---

## 4. The Database Layer (`api/utils/db.py`)

The database layer is built on `mysql-connector-python` with a **connection pool** of size 10:

```python
_pool = pooling.MySQLConnectionPool(pool_name="autobase", pool_size=10, **DB_CONFIG)
```

Three fetch helpers abstract all SQL execution:

| Function | Returns | Used for |
|---|---|---|
| `fetch_all(query, params)` | `list[dict]` | Any multi-row query; rows as named dicts |
| `fetch_one(query, params)` | `dict \| None` | Single-row lookups |
| `fetch_matrix(query)` | `(columns, rows)` | SELECT results for the chat tool (array-of-arrays format) |

Every value is passed through `_sanitize()`, which converts MySQL-specific types (`datetime`, `Decimal`, `bytes`, `timedelta`) into JSON-safe Python primitives before the data ever reaches FastAPI's serializer.

---

## 5. The Five Tools (`api/tools/handlers.py`)

These are the actual database operations the LLM can invoke. Each is a plain Python function taking `args: dict`.

### Tool 1 — `execute_sql`
- **Purpose:** Run a user-defined SELECT query.
- **Security guard:** Regex checks the query starts with `SELECT\s` (case-insensitive). Any non-SELECT query raises HTTP 400 immediately — the LLM **cannot** mutate data.
- **Returns:** `{query, columns, rows, row_count, duration_ms}` — column names and a 2D array of values, plus timing.
- Uses `fetch_matrix()` to get the columnar format the frontend renders as a table.

### Tool 2 — `get_schema`
- **Purpose:** Return the full schema of the connected database.
- Queries `information_schema.COLUMNS` joined with `information_schema.TABLES` to get all columns for all tables/views in one shot.
- Separately queries `information_schema.KEY_COLUMN_USAGE` to find foreign key relationships.
- Builds a structured dict per table with `{name, type, layer, columns[]}` — each column tagged as `PK`, `FK`, or `IDX`.
- Calls `infer_layer()` on each table name to assign it to `bronze`, `silver`, or `gold` tier based on naming conventions (`raw_`, `stg_`, `_clean`, `vw_`, `_summary`, etc.).

### Tool 3 — `get_table_lineage`
- **Purpose:** Build a lineage/dependency graph for all tables or a specific one.
- Queries three `information_schema` tables: `TABLES`, `VIEWS` (to get view SQL definitions), and `KEY_COLUMN_USAGE` (FK relationships).
- Constructs **nodes** (one per table/view, with layer and type annotations) and **edges** (source → target relationships).
- Edge types come from two sources:
  - **FK edges:** from `KEY_COLUMN_USAGE` — table references a parent table.
  - **VIEW edges:** parsed by scanning each view's `VIEW_DEFINITION` text for table name mentions. If a `JOIN` keyword appears, the edge is labeled `"JOIN"`, otherwise `"SELECT"`.
- If a specific `table_name` is given, it filters to only nodes/edges connected to that table (1-hop neighborhood).
- Returns `{lineage: {nodes[], edges[]}}` — this is exactly what the D3 force graph in the frontend consumes.

### Tool 4 — `describe_table`
- **Purpose:** Get a deep profile of a single table.
- Calls `validate_identifier()` first — this regex-validates the table name against `^[A-Za-z_][A-Za-z0-9_]*$` and returns it backtick-quoted. This is the **SQL injection prevention layer** for dynamic table names.
- Returns: total row count, last `UPDATE_TIME` from `information_schema`, 3 sample rows (via `LIMIT 3`), and the inferred data layer.

### Tool 5 — `check_data_quality`
- **Purpose:** Run automated quality checks on a table.
- Checks nullable columns for null counts, computing a percentage. Flags columns with >10% nulls as `"warn"`.
- Checks the primary key column for duplicate values: `COUNT(*) - COUNT(DISTINCT pk)`.
- Always appends a total row count check.
- Returns `{table, checks[]}` where each check has `{name, status: "ok"|"warn"|"error", detail}`.

### The Dispatcher
`dispatch_tool(tool, args)` is a simple string-keyed router that calls the right function. It's used both by the `/tool` HTTP endpoint (direct invocation) and by the LangGraph agent when it decides to call a tool.

---

## 6. The Tool Schemas (`api/utils/tools.json`)

This file defines all 5 tools in **OpenAI function-calling JSON schema format**:
```json
{
  "type": "function",
  "function": {
    "name": "execute_sql",
    "description": "Execute a SQL SELECT query...",
    "parameters": {
      "type": "object",
      "properties": {
        "query": { "type": "string", "description": "..." },
        "explanation": { "type": "string", "description": "..." }
      },
      "required": ["query", "explanation"]
    }
  }
}
```

These schemas serve as the **source of truth for tool descriptions**. They're loaded at startup into `TOOL_SCHEMAS` and indexed by name into `TOOL_SCHEMA_BY_NAME`. When `_build_langgraph_tools()` wraps each Python function in a LangChain `@tool` decorator, it pulls the `description` field from this dict — so the LLM's understanding of what each tool does comes directly from `tools.json`, not from hardcoded strings scattered through code.

---

## 7. The LangChain Tool Wrappers (`api/tools/helpers.py → _build_langgraph_tools`)

LangGraph requires tools to be **LangChain `@tool` decorated functions**, not raw Python functions. `_build_langgraph_tools()` creates these wrappers:

```python
@tool(description="Execute a SQL SELECT query against the MySQL database and return results.")
def execute_sql(query: str, explanation: str) -> str:
    result = run_tool_fn("execute_sql", {"query": query, "explanation": explanation})
    return json.dumps(result)
```

Each wrapper:
1. Is decorated with `@tool` and the description from `tools.json`.
2. Has typed parameters — LangChain uses these to auto-generate the JSON schema it sends to OpenAI.
3. Calls `run_tool_fn` (which is `AgenticWorkflow.run_tool`) to execute the real logic.
4. **Returns a JSON string** — LangGraph requires tool results to be strings or messages, so results are serialized.

This two-layer design (JSON schema → Python handler → LangChain tool) keeps tool logic clean and reusable both by the agent and by the direct `/tool` endpoint.

---

## 8. The LangGraph Agent (`_get_langgraph_agent`)

The actual LLM agent is built using:
```python
llm = ChatOpenAI(model=model, api_key=api_key, temperature=0)
agent = create_react_agent(
    model=llm,
    tools=langgraph_tools,   # the 5 @tool functions
    checkpointer=checkpointer,
    prompt=SYSTEM_PROMPT,
)
```

- **`ChatOpenAI`**: LangChain's wrapper around the OpenAI Chat Completions API. `temperature=0` means deterministic, factual responses — no creativity.
- **`create_react_agent`**: LangGraph's prebuilt ReAct (Reason + Act) agent. This creates a full **stateful state machine** graph. The ReAct loop is:
  1. LLM receives the conversation history and system prompt.
  2. LLM either calls a tool OR produces a final text response.
  3. If it calls a tool → execute the tool → append `ToolMessage` to state → go back to step 1.
  4. Repeat until the LLM produces a final text response (no tool call).
- **`checkpointer=InMemorySaver()`**: LangGraph persists the full message history for each `thread_id` in memory. This is what makes conversations **stateful** — subsequent messages in the same session have full context of prior tool calls and responses.
- **`prompt=SYSTEM_PROMPT`**: The agent always starts with this system context — "You are AutoBase, an expert data intelligence agent...". This tells the LLM about its capabilities, guidelines (never fabricate data, use backticks for column names, warn about slow queries), and the bronze/silver/gold data warehouse structure.
- The agent is **cached by `(api_key, model)` key** in `_agent_cache`. Once built, it's reused for all subsequent requests — no redundant initialization cost.

---

## 9. The AgenticWorkflow Class (`api/agent.py`)

`AgenticWorkflow` is the **central orchestration object** instantiated once at FastAPI startup. It owns:

| Attribute | Type | Purpose |
|---|---|---|
| `max_steps` | `int = 8` | Limits the ReAct loop to 8 tool calls (recursion_limit = 16 for LangGraph internals) |
| `history` | `list[dict]` | Local append-only log of every tool call made (tool name + args + result) |
| `_checkpointer` | `InMemorySaver` | Shared across all sessions; keyed by `thread_id` |
| `_agent_cache` | `dict` | Caches the built LangGraph agent by `(api_key, model)` |
| `_langgraph_tools` | `list` | The 5 LangChain `@tool` functions, built once in `__post_init__` |

### `run_tool(tool, args)` — Direct tool execution
Called by the `/tool` endpoint. Bypasses the LLM entirely. Calls `dispatch_tool`, logs the call to `self.history`, returns the result directly. This is how the Schema Explorer and Lineage Graph panels load their data — no LLM involved.

### `run_chat(user_message, session_id)` — Full agentic workflow

This is the most important method. Here's exactly what happens:

**Step 1 — Resolve config:**
```python
api_key = _resolve_api_key()   # reads OPENAI_API_KEY from env
model   = _resolve_model()     # reads OPENAI_MODEL from env
```

**Step 2 — Get or build the LangGraph agent** (from cache or freshly built).

**Step 3 — Context trimming:**
Before sending the new user message, the existing conversation state is loaded from the checkpointer. If previous messages exist, `trim_messages()` is applied (see §10). The trimmed history is written back via `agent.update_state()` before invoking.

**Step 4 — Invoke the agent:**
```python
result = agent.invoke(
    {"messages": [{"role": "user", "content": user_message}]},
    config={"configurable": {"thread_id": session_id}, "recursion_limit": 16},
)
```
LangGraph runs the ReAct loop autonomously — potentially calling multiple tools, getting results, reasoning, calling more tools — until it produces a final text response or hits `recursion_limit`.

**Step 5 — Parse the response:**
`result["messages"]` contains the **entire conversation history** for this thread. The code slices to `new_messages` (only messages added during this invocation) using the `existing_messages_count` snapshot taken in Step 3.

It then walks through new messages to:
- For each `AIMessage` with `tool_calls`: capture `{name, args}` into `tool_trace_map` keyed by call ID.
- For each `ToolMessage`: look up the matching call ID and attach the deserialized result to the trace entry.
- For each `AIMessage` without tool calls: extract the final text response.

**Step 6 — Return:**
```python
{
    "reply": "Here are the top 5 most driven cars...",
    "tool_calls": [
        {"name": "execute_sql", "args": {"query": "SELECT...", "explanation": "..."}, "result": {...}},
    ],
    "session_id": "abc-123"
}
```

The frontend receives this object and renders both the `reply` text and each tool call as a collapsible inline widget.

---

## 10. Context Window Management (`api/utils/trimmer.py`)

Long conversations would eventually overflow the LLM's context window. The trimmer runs **before each new message is sent**:

**Strategy (4 steps in order):**

1. **Preserve system messages** — `SystemMessage` objects are always separated and prepended back at the end, unconditionally.

2. **Truncate oversized tool results** — Any `ToolMessage` whose `.content` exceeds `max_tool_result_chars` (default 4,000) is shallow-copied and its content replaced with `content[:4000] + "\n... [truncated from N chars]"`. Database query results can be huge; this prevents them from eating the entire context budget.

3. **Keep last N conversational turns** — Walk backwards through the message list, counting `HumanMessage` objects. Once `keep_last_n_pairs` (default 10) user messages have been seen, cut everything before that point. This preserves the most recent 10 full Q&A exchanges including all their intermediate tool calls.

4. **Hard cap** — After applying the N-pairs window, if the list still exceeds `max_messages` (default 40), truncate from the oldest end. Then: if the list starts with an orphaned `ToolMessage` (which would be invalid — a ToolMessage must follow an AI message with a tool call), pop it until we reach a valid message.

All settings are configurable at runtime via `GET/POST /context-config` and are exposed as controls in the frontend sidebar (Max Messages, Keep Last N Turns, Max Tool Result Chars, Trim Enabled checkbox).

---

## 11. The FastAPI Routes (`api/app.py`)

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/` | Serves `front/index.html` |
| `GET` | `/favicon.ico` | Serves favicon or 204 |
| `POST` | `/ping` | Tests DB connection; returns all tables/views with name, type, row count |
| `POST` | `/tool` | Direct tool dispatch: `{tool: "execute_sql", args: {...}}` → result dict |
| `POST` | `/chat` | Full agentic chat: `{message: "...", session_id: "..."}` → `{reply, tool_calls, session_id}` |
| `GET` | `/context-config` | Read current trim settings |
| `POST` | `/context-config` | Update trim settings at runtime |

CORS is set to `allow_origins=["*"]` — any origin can call the API, which is important since the frontend is served from the same origin but could be opened from file:// too.

The `AgenticWorkflow` instance is shared across all routes — this is intentional and safe because LangGraph's checkpointer handles per-session isolation internally.

---

## 12. The Frontend (`front/`)

The frontend is a **single-page application** — pure HTML, CSS, and vanilla JavaScript. No framework. No bundler. It's served as static files from FastAPI's `/front` mount point.

### How the frontend is connected to the backend

**All backend communication** goes through one function:
```javascript
async function callBackend(endpoint, body = {}) {
  const url = state.backendUrl + endpoint;  // e.g., "http://localhost:8000/chat"
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  return res.json();
}
```

The backend URL is configurable in the sidebar input (default `http://localhost:8000`). **No API keys are ever sent from the frontend.** OpenAI authentication is handled entirely server-side via environment variables.

### Connection flow
1. User clicks "Connect" → `connectToBackend()` calls `POST /ping`.
2. On success: loads `state.tableList`, calls `loadSchema()` (`/tool` with `get_schema`), calls `loadLineage()` (`/tool` with `get_table_lineage`), loads context config.
3. On failure: `addDemoMode()` activates — a hardcoded car database schema and lineage is loaded, allowing the UI to be explored without a backend.

### The Three Panels

**Query Agent (Chat):**
- User types a message → `sendMessage()` → `runAgent(text)` → `POST /chat` with `{message, session_id}`.
- `session_id` is a UUID generated with `crypto.randomUUID()` once at page load. It's sent with every message, allowing the backend to maintain per-browser-session conversation state in LangGraph's checkpointer.
- The response `{reply, tool_calls}` is rendered:
  - Each `tool_call` in `tool_calls[]` is shown as a collapsible `⟳ TOOL · <name>` widget with the args as JSON. SQL queries get a syntax-highlighted SQL block.
  - The `reply` text is passed through a lightweight Markdown renderer (bold, code, italic, paragraphs).
  - If a tool call was `get_schema` or `get_table_lineage`, `handleToolSideEffect()` automatically updates the Schema Explorer and Lineage Graph panels with the fresh data.

**Schema Explorer:**
- Calls `GET /tool → get_schema` directly (bypasses the LLM).
- Renders each table as a card with layer color-coding (bronze/silver/gold), type badges (TABLE/VIEW), and a column grid showing name, type, key status (PK/FK/IDX), and nullability.
- Has a live text filter that hides cards not matching the query.

**Lineage Graph:**
- Calls `GET /tool → get_table_lineage` with `{table_name: "all"}`.
- Renders the `{nodes[], edges[]}` response using **D3.js v7 force simulation**:
  - Nodes are circles color-coded by layer and type (yellow = base table, cyan = view, orange = focused node).
  - Edges are directed arrows labeled with relationship type (FK, JOIN, SELECT, GROUP BY).
  - Forces: link force (distance 180, strength 0.8), repulsion charge (-500), center force, plus a horizontal X-force that pushes bronze tables left, silver tables center, and gold tables/views right — creating a natural left-to-right data flow layout.
  - Supports pan (drag), zoom (scroll or +/− buttons), and focus (dropdown to filter to a table's neighborhood).
  - Clicking a node shows a tooltip and calls `describe_table` on it via the `/tool` endpoint.

### Demo Mode
If `/ping` fails, the frontend activates demo mode — injecting a hardcoded `DEMO_SCHEMA` and `DEMO_LINEAGE` object directly into `state`. All tool calls are intercepted by `executeToolDemo()` which returns canned responses with a 400ms artificial delay. Chat in demo mode is blocked with a message explaining the backend is needed.

> **Note:** `DEMO_SCHEMA`/`DEMO_LINEAGE` (the `cars_bronze`/`cars_silver`/`brands`/`vw_cars_gold`/`vw_price_summary` car-sales warehouse) are offline fixture data for rendering the UI when no backend is reachable — they are not the live database schema. The agent's system prompt instead introspects whatever schema is actually connected at runtime (see `api/semantics/loader.py`).

---

## 13. The Full Agentic Workflow — End-to-End Trace

Here is a complete trace of what happens when a user types **"Which make has the highest average price?"**:

```
Browser                       FastAPI                    LangGraph/OpenAI          MySQL
  │                              │                              │                    │
  │ POST /chat                   │                              │                    │
  │ {message:"Which make...",    │                              │                    │
  │  session_id:"abc-123"}       │                              │                    │
  │──────────────────────────────▶                              │                    │
  │                              │ AgenticWorkflow.run_chat()   │                    │
  │                              │ 1. Load prior state          │                    │
  │                              │ 2. trim_messages()           │                    │
  │                              │ 3. agent.invoke(...)         │                    │
  │                              │──────────────────────────────▶                    │
  │                              │                  [ReAct turn 1]                   │
  │                              │                  LLM receives:                    │
  │                              │                  - SYSTEM_PROMPT                  │
  │                              │                  - HumanMessage: "Which make..."  │
  │                              │                  LLM decides: call get_schema      │
  │                              │◀ ─ ─ ─ ─ ─ ─ ─ ─ tool_call: get_schema()         │
  │                              │ handlers.get_schema_tool()   │                    │
  │                              │────────────────────────────────────────────────────▶
  │                              │   SELECT c.TABLE_NAME, c.COLUMN_NAME...           │
  │                              │◀────────────────────────────────────────────────── │
  │                              │ → ToolMessage: {"schema":[...]}                    │
  │                              │──────────────────────────────▶                    │
  │                              │                  [ReAct turn 2]                   │
  │                              │                  LLM receives full schema         │
  │                              │                  LLM decides: call execute_sql     │
  │                              │◀ ─ ─ ─ ─ ─ ─ ─ ─ tool_call: execute_sql(         │
  │                              │                    query: "SELECT make,            │
  │                              │                      AVG(price_usd) AS avg_price   │
  │                              │                      FROM cars_silver              │
  │                              │                      GROUP BY make                 │
  │                              │                      ORDER BY avg_price DESC       │
  │                              │                      LIMIT 1",                     │
  │                              │                    explanation: "...")             │
  │                              │ handlers.execute_sql_tool()  │                    │
  │                              │   regex check: SELECT ✓      │                    │
  │                              │────────────────────────────────────────────────────▶
  │                              │   [query executes in pool]                        │
  │                              │◀────────────────────────────────────────────────── │
  │                              │ → ToolMessage: {columns, rows, row_count, ms}      │
  │                              │──────────────────────────────▶                    │
  │                              │                  [ReAct turn 3]                   │
  │                              │                  LLM receives query result        │
  │                              │                  LLM produces FINAL text response  │
  │                              │◀─────────────────────────────                     │
  │                              │ Parse new messages:          │                    │
  │                              │ - extract tool_trace_map     │                    │
  │                              │ - extract reply_text         │                    │
  │                              │ Return {reply, tool_calls,   │                    │
  │                              │         session_id}          │                    │
  │◀──────────────────────────── │                              │                    │
  │ Render in browser:           │                              │                    │
  │ ⟳ TOOL · get_schema {...}    │                              │                    │
  │ ⟳ TOOL · execute_sql         │                              │                    │
  │   SQL: SELECT make, AVG...   │                              │                    │
  │ AUTOBASE AGENT:              │                              │                    │
  │   "Mercedes-Benz has the     │                              │                    │
  │    highest avg price at      │                              │                    │
  │    $68,900..."               │                              │                    │
```

The conversation state (all 5+ messages: HumanMessage, AIMessage with tool_calls, ToolMessage×2, final AIMessage) is **persisted in LangGraph's `InMemorySaver`** under `thread_id="abc-123"`. The next user message in that browser session will start with this full history already loaded — no re-fetching, no re-explaining, full contextual memory.

---

## 14. Key Design Decisions

| Decision | Why |
|---|---|
| All LLM logic on the backend | API keys never leave the server; frontend is dumb |
| LangGraph ReAct agent | Handles multi-step tool chaining natively; built-in state persistence via checkpointer |
| `tools.json` for schemas | Single source of truth for tool descriptions; decoupled from Python code |
| MySQL connection pool (size 10) | Thread-safe; reuses connections across concurrent requests |
| SQL injection prevention | `validate_identifier()` regex + backtick quoting for dynamic identifiers; regex guard for SELECT-only on user queries |
| Context trimmer | Prevents token overflow in long sessions; truncates large tool results first (most common cause of bloat) |
| Demo mode | App is explorable without a running MySQL instance |
| `temperature=0` | Deterministic SQL generation; factual data responses |
| Agent cache by `(api_key, model)` | Building a LangGraph agent is expensive; cached once and reused for all sessions |

---

## 15. LangChain & LangGraph — Deep Dive

---

### The Big Picture: Two Libraries, One Pipeline

**LangChain** and **LangGraph** are related but serve completely different roles. Think of them as layers:

```
┌─────────────────────────────────────────────────────┐
│                   LangGraph                         │
│  (the execution engine — state machine, loop, memory)│
│                                                     │
│   ┌─────────────────────────────────────────────┐   │
│   │              LangChain Core                 │   │
│   │  (the vocabulary — Messages, tools, LLM     │   │
│   │   abstraction, @tool decorator)             │   │
│   └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

LangChain defines the **building blocks**. LangGraph defines the **execution graph** that orchestrates those blocks. AutoBase uses both.

---

### LangChain's Role

#### `langchain_core.messages` — The Message Type System

Imported directly in `agent.py`:
```python
from langchain_core.messages import AIMessage, ToolMessage
```
And in `trimmer.py`:
```python
from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
)
```

LangChain defines a **typed message hierarchy** that maps exactly to the OpenAI Chat API roles:

| LangChain Class | OpenAI Role | Contains |
|---|---|---|
| `SystemMessage` | `system` | The `SYSTEM_PROMPT` persona/guidelines |
| `HumanMessage` | `user` | The user's typed question |
| `AIMessage` | `assistant` | LLM's reply text **or** tool call requests |
| `ToolMessage` | `tool` | The result returned after a tool executes |
| `BaseMessage` | (base class) | Used for `isinstance` checks in the trimmer |

This matters because the entire conversation history stored in LangGraph is a `list[BaseMessage]`. When `run_chat` slices `all_messages[existing_messages_count:]`, it's slicing this typed list. When the trimmer walks it, it uses `isinstance(msg, HumanMessage)` — it's reading the **types**, not inspecting string content. This is far more reliable than parsing raw dicts.

The `AIMessage` type carries a special attribute: `.tool_calls` — a list of structured `{id, name, args}` dicts when the LLM decided to call a tool instead of responding. AutoBase reads this directly:
```python
if isinstance(message, AIMessage):
    if message.tool_calls:
        for call in message.tool_calls:
            tool_trace_map[call["id"]] = {"name": call["name"], "args": call["args"], "result": None}
```

The `ToolMessage` carries `.tool_call_id` — the matching ID that links it back to the `AIMessage` that requested it. AutoBase uses this to stitch together the tool trace:
```python
if isinstance(message, ToolMessage):
    tool_call_id = str(getattr(message, "tool_call_id", ""))
    if tool_call_id in tool_trace_map:
        tool_trace_map[tool_call_id]["result"] = _safe_tool_content(message.content)
```

Without LangChain's typed message system, you'd be parsing raw dictionaries and string-matching roles — brittle and error-prone.

---

#### `langchain_core.tools.tool` — The `@tool` Decorator

Imported in `helpers.py`:
```python
from langchain_core.tools import tool
```

This decorator transforms a plain Python function into a **LangChain StructuredTool** object. Here's what it actually does under the hood:

```python
@tool(description="Execute a SQL SELECT query against the MySQL database and return results.")
def execute_sql(query: str, explanation: str) -> str:
    result = run_tool_fn("execute_sql", {"query": query, "explanation": explanation})
    return json.dumps(result)
```

When LangGraph passes this tool to OpenAI's API, it needs to describe it as a JSON schema. LangChain's `@tool` does this automatically by:
1. Reading the **function name** → becomes the tool name (`"execute_sql"`).
2. Reading the `description=` argument → becomes the tool description sent to OpenAI.
3. **Inspecting the Python type hints** (`query: str`, `explanation: str`) → auto-generates the `parameters` JSON schema with `{"type": "string"}` entries and marks them as `required`.
4. Wrapping the function so it can be called by LangGraph's executor node.

This is why `tools.json` and the `@tool` decorator work together: `tools.json` provides the human-written descriptions (pulled via `_tool_description()`), while `@tool` handles the schema generation from type hints. You never manually write `{"type": "object", "properties": {"query": {"type": "string"}}}` — LangChain generates it.

The resulting object has:
- `.name` → `"execute_sql"`
- `.description` → the string from `tools.json`
- `.args_schema` → a Pydantic model auto-generated from the type hints
- `.invoke(args)` → callable by LangGraph's executor

All five tools are built this way and returned as a list to `create_react_agent`.

---

#### `langchain_openai.ChatOpenAI` — The LLM Abstraction

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model=model, api_key=api_key, temperature=0)
```

`ChatOpenAI` is LangChain's wrapper around `openai.OpenAI().chat.completions.create(...)`. It does several things beyond a raw OpenAI client call:

- **Message format translation**: Converts LangChain's `[HumanMessage, AIMessage, ToolMessage, ...]` list into OpenAI's `[{"role": "user", "content": "..."}, ...]` dict format automatically.
- **Tool binding**: When LangGraph calls `llm.bind_tools(tools)` internally, `ChatOpenAI` serializes all the `@tool` schemas into the `tools=[...]` parameter that OpenAI's API expects for function calling.
- **Response parsing**: Converts OpenAI's raw response (which has `choices[0].message.tool_calls` for tool calls, or `choices[0].message.content` for text) back into typed LangChain `AIMessage` objects with proper `.tool_calls` attribute.
- **Retry/error handling**: LangChain adds retry logic around transient API errors.

`temperature=0` is passed through directly to the OpenAI API. This makes the LLM produce the most deterministic output — critical for SQL generation where consistency matters more than creativity.

The `ChatOpenAI` instance never gets called directly in AutoBase's code. It's handed to `create_react_agent` and LangGraph takes it from there.

---

### LangGraph's Role

#### `langgraph.prebuilt.create_react_agent` — The Agent Graph

This is the most important single function call in the entire project:

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(
    model=llm,
    tools=langgraph_tools,
    checkpointer=checkpointer,
    prompt=SYSTEM_PROMPT,
)
```

`create_react_agent` builds a **compiled LangGraph state machine** — a directed graph of nodes and edges that implements the ReAct (Reason + Act) pattern. Here's what the graph looks like internally:

```
          ┌─────────────────────────────────┐
          │         __start__               │
          └──────────────┬──────────────────┘
                         │ (inject system prompt + user message)
                         ▼
          ┌─────────────────────────────────┐
          │         agent node              │  ← calls ChatOpenAI
          │  (LLM reasons over messages,    │
          │   decides: tool call or end)    │
          └──────────────┬──────────────────┘
                         │
              ┌──────────┴──────────┐
              │                     │
   [has tool_calls]         [no tool_calls → text]
              │                     │
              ▼                     ▼
  ┌──────────────────────┐    ┌─────────────┐
  │    tools node        │    │   __end__   │
  │ (executes each tool, │    └─────────────┘
  │  appends ToolMessage)│
  └──────────┬───────────┘
             │ (loop back)
             ▼
      ┌─────────────────┐
      │   agent node    │  ← LLM sees tool results, reasons again
      └─────────────────┘
```

The graph has exactly **two action nodes**:

1. **`agent` node**: Calls `ChatOpenAI` with the full message history. The LLM responds with either (a) `tool_calls` in the `AIMessage` — triggering an edge to the `tools` node — or (b) a plain text response — triggering an edge to `__end__`.

2. **`tools` node**: LangGraph's prebuilt `ToolNode`. It iterates over every tool call in the last `AIMessage`, calls each LangChain tool's `.invoke(args)` method, and appends a `ToolMessage` for each result. Then it routes unconditionally back to the `agent` node.

This loop is what makes it "agentic" — the LLM is not called once; it's called **repeatedly** until it decides it's done. Each iteration it sees more tool results and can decide to call more tools or produce its final answer. In AutoBase's case, a typical question might loop 2–3 times: first `get_schema` to understand the table structure, then `execute_sql` to run the actual query, then a final text response.

The `recursion_limit` in the config:
```python
config = {"configurable": {"thread_id": session_id}, "recursion_limit": self.max_steps * 2}
```
...limits the total number of **graph node visits** (each agent→tools→agent counts as 2), preventing infinite loops if the LLM gets stuck calling tools repeatedly.

---

#### `langgraph.checkpoint.memory.InMemorySaver` — The Session Memory

```python
from langgraph.checkpoint.memory import InMemorySaver

_checkpointer: InMemorySaver = field(default_factory=InMemorySaver, init=False, repr=False)
```

One `InMemorySaver` instance is created when `AgenticWorkflow` is instantiated (at FastAPI startup) and lives for the lifetime of the server process.

The checkpointer is LangGraph's **state persistence layer**. Every time `agent.invoke()` completes a full ReAct loop, LangGraph automatically:
1. Serializes the entire `{"messages": [...]}` state.
2. Saves it to the checkpointer keyed by `thread_id` (the `session_id` from the frontend).

On the next call to `agent.invoke()` with the same `thread_id`, LangGraph:
1. Loads the saved state from the checkpointer.
2. Appends the new user message to the existing history.
3. Runs the ReAct loop with full conversation context.

This is why conversations in AutoBase are **stateful across HTTP requests** without any session management code in FastAPI itself. The HTTP layer is stateless (each `/chat` POST is independent), but LangGraph's checkpointer bridges the gap. The `session_id` UUID generated in the browser's JavaScript is the only thing connecting messages — it's passed as `thread_id` every time, and LangGraph does the rest.

`InMemorySaver` stores everything in a Python dict in RAM. It's simple and fast, but ephemeral — if the server restarts, all conversation history is lost. For a production system you'd swap it for `SqliteSaver` or `PostgresSaver` (both exist in the LangGraph ecosystem) without changing any application code.

The checkpointer is also used to **mutate state before invoking**, which is how the context trimmer works:
```python
# Read current state
prior_state = agent.get_state(config)
prior_messages = prior_state.values.get("messages")

# Trim it
trimmed = trim_messages(prior_messages)

# Write trimmed state back BEFORE invoking
agent.update_state(config, {"messages": trimmed})
```

`agent.get_state(config)` and `agent.update_state(config, patch)` are LangGraph APIs that reach directly into the checkpointer for a given `thread_id`. AutoBase uses this to surgically rewrite the message history before each LLM call — trimming old messages to prevent token overflow — without disrupting the session identity.

---

### How They Wire Together — The Exact Call Chain

Here is the precise sequence of library calls on a single `/chat` request:

```
POST /chat  →  AgenticWorkflow.run_chat()
                │
                ├─ _resolve_api_key()        [reads env var, pure Python]
                ├─ _resolve_model()          [reads env var, pure Python]
                │
                ├─ _get_langgraph_agent()    [LangChain + LangGraph]
                │   ├─ ChatOpenAI(model, api_key, temperature=0)
                │   │     └─ langchain_openai wraps openai.Client
                │   └─ create_react_agent(llm, tools, checkpointer, prompt)
                │         └─ builds compiled StateGraph internally
                │             ├─ agent node  ← uses ChatOpenAI
                │             └─ tools node  ← uses ToolNode([execute_sql, ...])
                │
                ├─ agent.get_state(config)   [LangGraph: reads InMemorySaver]
                ├─ trim_messages(msgs)        [custom trimmer, uses langchain_core types]
                ├─ agent.update_state(config) [LangGraph: writes InMemorySaver]
                │
                └─ agent.invoke(input, config)  [LangGraph: runs the graph]
                    │
                    ├─ [agent node] ChatOpenAI.invoke(messages + tools)
                    │     └─ openai API call → AIMessage(tool_calls=[get_schema])
                    │
                    ├─ [tools node] get_schema LangChain @tool
                    │     └─ run_tool_fn("get_schema", {})
                    │           └─ dispatch_tool() → get_schema_tool() → MySQL
                    │     → ToolMessage(content=json.dumps(schema))
                    │
                    ├─ [agent node] ChatOpenAI.invoke(messages + tool result)
                    │     └─ openai API call → AIMessage(tool_calls=[execute_sql])
                    │
                    ├─ [tools node] execute_sql LangChain @tool
                    │     └─ run_tool_fn("execute_sql", {query: "SELECT..."})
                    │           └─ execute_sql_tool() → MySQL
                    │     → ToolMessage(content=json.dumps(rows))
                    │
                    └─ [agent node] ChatOpenAI.invoke(messages + all tool results)
                          └─ openai API call → AIMessage(content="Mercedes has...")
                               └─ no tool_calls → graph ends

                    ← returns {"messages": [all 6 messages]}
```

---

### Why Not Use LangChain Chains?

LangChain also provides "chains" (e.g., `LLMChain`, `SequentialChain`, LCEL pipelines with `|` operators). AutoBase does **not** use any of these. Here's why:

- **Chains are linear** — they define a fixed sequence of steps (prompt → LLM → output parser). They can't loop.
- **Agents require cycles** — the LLM needs to call tools, see results, possibly call more tools, then decide when to stop. That's a **cycle in the graph**, which chains fundamentally cannot represent.
- **LangGraph replaces chains for agentic workflows** — `create_react_agent` gives you a compiled graph with proper cycle support, interrupt/resume capability, and the checkpointer for persistence. It's strictly more powerful.

The only LangChain pieces AutoBase uses are the **building blocks** (`@tool`, `ChatOpenAI`, message types) — not the chain orchestration. LangGraph handles all orchestration.

---

### Summary: What Each Import Does

| Import | Package | Purpose in AutoBase |
|---|---|---|
| `from langchain_core.messages import AIMessage, ToolMessage` | `langchain-core` | Reading message types to extract tool traces and reply text from agent output |
| `from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage` | `langchain-core` | Type-checking messages in the context trimmer |
| `from langchain_core.tools import tool` | `langchain-core` | `@tool` decorator — wraps Python functions into LangChain tools with auto-generated JSON schemas |
| `from langchain_openai import ChatOpenAI` | `langchain-openai` | OpenAI LLM abstraction — handles message format translation, tool binding, response parsing |
| `from langgraph.prebuilt import create_react_agent` | `langgraph` | Builds the compiled ReAct state machine graph (agent node ↔ tools node loop) |
| `from langgraph.checkpoint.memory import InMemorySaver` | `langgraph` | In-process session memory — persists `list[BaseMessage]` per `thread_id` across HTTP requests |
