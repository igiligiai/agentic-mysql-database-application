# agentic-mysql-database-application

**AutoBase** is a full-stack, agentic data intelligence application. It lets you hold a
natural-language conversation with a MySQL database — ask something like *"which make has the
highest average price?"* and get back real SQL results, rendered tables, lineage graphs, and
plain-English explanations.

The entire intelligence loop runs on the **Python backend**; the browser is a passive display
layer. OpenAI credentials never leave the server.

> The narrative above (and the `cars_*` table names used as examples throughout this README)
> describe a demo car-sales schema used for offline/demo mode. The database AutoBase is
> actually pointed at is whatever schema `DB_NAME` resolves to — the agent introspects it live
> via `information_schema` and does not assume any fixed table names.

Python-first agentic MySQL application with two main folders:

- api: FastAPI backend and database tools
- front: static frontend implementation

## How it works

1. The frontend posts `{message, session_id}` to `POST /chat`. The `session_id` is a UUID
	 generated once per browser session.
2. `AgenticWorkflow` (in `api/agent.py`) loads the prior conversation state from LangGraph's
	 checkpointer, trims it to fit the context window, and invokes a LangGraph **ReAct** agent
	 built with `create_react_agent` over `ChatOpenAI(temperature=0)`.
3. The agent reasons and chains tool calls autonomously (schema lookup → SQL generation →
	 execution → explanation), capped at 8 steps / recursion limit 16.
4. Tool results and the final reply are returned as `{reply, tool_calls, session_id}` and
	 rendered in the UI, with `get_schema` / `get_table_lineage` results also refreshing the
	 Schema Explorer and Lineage Graph panels.

The Schema Explorer and Lineage Graph panels call `POST /tool` directly, bypassing the LLM
entirely — no token cost for plain data browsing.

## Architecture at a glance

| Layer | Module | Responsibility |
| --- | --- | --- |
| Entrypoint | `main.py` | Starts uvicorn on `PORT` (default 8000) |
| HTTP | `api/app.py` | Routes, CORS, static mount of `front/` |
| Orchestration | `api/agent.py` | `AgenticWorkflow`: agent cache, checkpointer, tool history |
| Tools | `api/tools/handlers.py`, `api/tools/sql_tools.py` | Metadata tools and SQL-executing tools respectively, both routed through `dispatch_tool()` |
| Tool wiring | `api/tools/helpers.py` | LangChain `@tool` wrappers + agent factory |
| SQL safety | `api/utils/sql_guard.py` | AST-based validation/rewriting of agent-authored SQL before execution |
| Error contract | `api/utils/errors.py` | The structured, redacted `ToolError` shape tools return instead of raising |
| Semantic layer | `api/semantics/loader.py` | Live `information_schema` inventory rendered into the system prompt |
| Schemas | `api/utils/tools.json` | Single source of truth for tool descriptions |
| Database | `api/utils/db.py` | MySQL connection pool (size 10) + JSON-safe fetch helpers |
| Context | `api/utils/trimmer.py` | Context-window trimming strategy |
| UI | `front/` | Vanilla HTML/CSS/JS SPA, D3 v7 force graph — no framework, no bundler |

## The six tools

| Tool | What it does |
| --- | --- |
| `execute_sql` | Runs a SELECT query, validated and rewritten by the AST guard; returns columns, rows, row count, and duration |
| `explain_query` | Returns `EXPLAIN` cost/row-estimate output for a SELECT without running it |
| `get_schema` | Full schema from `information_schema`, with PK/FK/IDX tags and bronze/silver/gold layer inference |
| `get_table_lineage` | Builds `{nodes, edges}` from FK relationships and parsed view definitions |
| `describe_table` | Row count, last update time, 3 sample rows, inferred layer |
| `check_data_quality` | Null-ratio checks (>10% → warn), PK duplicate detection, total row count |

## Context window management

Long sessions are trimmed before each new message, in order:

1. System messages are always preserved.
2. Oversized `ToolMessage` content is truncated (default 4,000 chars) — query results are the
	 most common source of context bloat.
3. Only the last N conversational turns are kept (default 10), including their tool calls.
4. A hard cap on total messages (default 40), with orphaned `ToolMessage` cleanup.

All four settings are tunable at runtime via `GET/POST /context-config` and exposed in the
frontend sidebar.

## Security model

- **AST-validated, read-only SQL:** `execute_sql` and `explain_query` are parsed with sqlglot
	(`api/utils/sql_guard.py`) before anything runs. Multi-statement input, any DML/DDL node
	anywhere in the tree, `SELECT ... INTO`, `SLEEP`/`BENCHMARK`/`LOAD_FILE`-style functions
	(matched regardless of backtick-quoting), locking reads (`FOR UPDATE` / `LOCK IN SHARE MODE`),
	and out-of-scope schema qualifiers are all rejected; a missing or oversized `LIMIT` is injected
	or clamped rather than left to the caller. The LLM cannot mutate data.
- **Structured, redacted tool errors:** failures come back as a typed `ToolError` dict
	(`api/utils/errors.py` is the single source of truth for the error-code table) instead of a
	raw exception, so the agent can self-correct retryable failures. A redaction pass strips
	credentials, hosts, and filesystem paths before any driver text reaches the LLM or an HTTP
	response.
- **Identifier validation:** dynamic table names pass through `validate_identifier()`
	(`^[A-Za-z_][A-Za-z0-9_]*$`) and are backtick-quoted before interpolation.
- **Server-side credentials:** `OPENAI_API_KEY` and `OPENAI_MODEL` are read from the
	environment; the frontend never sends keys or model selection.

## Demo mode

If `POST /ping` fails, the frontend falls back to a hardcoded demo schema and lineage so the UI
can be explored without a running MySQL instance. Chat is disabled in this mode.

## Folder structure

- api/app.py: FastAPI backend with tool routes
- api/agent.py: Python-managed agent state, OpenAI initialization, and tool binding
- front/index.html: UI shell
- front/styles.css: UI styles
- front/app.js: frontend logic and backend chat calls
- main.py: local launcher for the FastAPI app

## Features

- POST /ping for database object discovery
- POST /tool dispatcher with tools:
	- execute_sql
	- explain_query
	- get_schema
	- get_table_lineage
	- describe_table
	- check_data_quality
- POST /chat for Python-side OpenAI chat + tool-calling orchestration
- GET/POST /context-config to read and update trim settings at runtime
- GET /metrics for per-turn telemetry (latency, token counts, tool/error shapes — no message content)
- LangGraph-based workflow for stateful agent execution and tool routing
- AST-validated (sqlglot), read-only SQL guard for execute_sql / explain_query, with LIMIT injection/clamping
- Structured, redacted tool-error contract that drives bounded agent self-repair
- Live `information_schema` inventory rendered into the system prompt (no hand-curated glossary)
- D3 lineage graph, schema explorer, and chat UI

## Prerequisites

- Python 3.13+
- Local MySQL database

## Environment variables

Set these in your shell or environment file:

- DB_HOST (default: localhost)
- DB_USER (default: root)
- DB_PASSWORD (default: empty)
- DB_NAME (default: cars_db)
- DB_PORT (default: 3306)
- PORT (default: 8000)
- OPENAI_API_KEY (required)
- OPENAI_MODEL (required)
- QUERY_MAX_ROWS (default: 1000) — hard ceiling the SQL guard enforces by rewriting/clamping `LIMIT`
- QUERY_TIMEOUT_MS (default: 5000) — per-connection statement timeout applied in `api/utils/db.py`

OpenAI tool-calling initialization happens in Python backend (`api/agent.py`).
Frontend does not send API keys.
Frontend does not send model selection.

## Agent Workflow

- The backend uses LangGraph + LangChain OpenAI to orchestrate tool calls.
- Thread state is persisted per `session_id` via LangGraph checkpointer (`thread_id`).
- No direct use of `client.chat.completions` in the application workflow.

## Run

1. Install dependencies

```bash
pip install -e .
```

2. Start the API

```bash
python main.py
```

3. Open the app

- http://localhost:8000/

## Notes

- See `docs/explainer.md` for a full end-to-end walkthrough, including a request trace and a LangChain/LangGraph deep dive.
- Legacy files autobase_server.js and car_data_agent.html are preserved as migration references.
- Frontend defaults to backend URL http://localhost:8000.

## Testing

```bash
uv run pytest              # fast suite: guard, errors, semantics, telemetry
uv run pytest -m eval      # agent evaluation; needs OPENAI_API_KEY and MySQL
```

`tests/eval/fixture.sql` recreates a schema and a deterministic seed for the evaluation corpus:

```bash
mysql -u root -p -e "CREATE DATABASE autobase_eval"
mysql -u root -p autobase_eval < tests/eval/fixture.sql
```

The default `pytest` invocation skips the `eval` marker (see `pyproject.toml`), since it makes
live LLM calls and needs a reachable database; run it explicitly and only when you intend to
spend tokens.
