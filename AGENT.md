# AGENT.md — Working Guide for AI Coding Agents

This file is the **entry point for an AI agent implementing new features in AutoBase**. It is
forward-looking: it tells you where to start, what the current agentic ceiling is, and how to
raise it.

## Read these first

| Document | What it gives you | When to read it |
| --- | --- | --- |
| [`.github/copilot.instructions.md`](.github/copilot.instructions.md) | **Normative rules.** Repo layout, architecture patterns, the 6-step "Adding a New Tool" recipe, security requirements, code style, common pitfalls. | Before writing any code. |
| [`docs/explainer.md`](docs/explainer.md) | **Narrative explanation.** End-to-end request trace, LangChain/LangGraph deep dive, design-decision rationale. | When you need to understand *why*, not just *what*. |
| [`README.md`](README.md) | Summary, endpoints, env vars, run instructions. | Orientation / onboarding. |

`.github/copilot.instructions.md` wins on any conflict. This file never restates its rules — it
links to them. In particular, do **not** re-derive the tool-creation steps here; follow
§6 "Adding a New Tool" in the instructions file verbatim.

---

## 1. Mental model in 30 seconds

```
POST /chat → AgenticWorkflow.run_chat()   (api/agent.py)
               ├── trim_messages()         (api/utils/trimmer.py)
               ├── _get_langgraph_agent()  (api/tools/helpers.py, cached by (api_key, model))
               └── create_react_agent.invoke()
                      └── @tool wrapper → AgenticWorkflow.run_tool()
                             └── dispatch_tool() → *_tool() → db.fetch_*()
```

Three invariants you must not break:

1. **One agent, cached.** `_agent_cache` is keyed by `(api_key, model)`. Never build a
   `ChatOpenAI` or agent per request.
2. **One source of truth per concern.** Tool descriptions live in `api/utils/tools.json`.
   Session memory lives in the LangGraph checkpointer. Trim settings live in `trimmer._settings`.
   `AgenticWorkflow.history` is a debug log, not state.
3. **The LLM is read-only against the database.** The `^SELECT\s` guard in `execute_sql_tool`
   and `validate_identifier()` are load-bearing security controls.

---

## 2. Current agentic ceiling

Know the limits before you try to extend them. These are the real constraints in the code today:

| Area | Today | Consequence |
| --- | --- | --- |
| Memory | `InMemorySaver` in `AgenticWorkflow._checkpointer` | All conversation state is lost on restart; no horizontal scaling |
| Loop depth | `max_steps = 8`, `recursion_limit = max_steps * 2` | Deep multi-tool chains silently hit the limit |
| Response mode | `agent.invoke()` — blocking | No token streaming; long queries look frozen in the UI |
| Tool surface | 5 read-only tools | No write path, no profiling, no cross-database joins |
| Recovery | Tool errors raise `HTTPException` straight up | A single bad SQL guess ends the turn instead of being retried |
| Context | Heuristic trimming (counts, not tokens) | Token budget is approximated, never measured |
| Observability | `history` list + nothing else | No latency, token, or cost attribution per turn |
| Evaluation | None | No regression signal when the prompt or tools change |
| Concurrency | Sync FastAPI handlers calling blocking `mysql-connector` | A slow query blocks a worker thread |

---

## 3. High-leverage improvements

Ordered roughly by value-to-effort. Each entry names the files you will touch.

### 3.1 Streaming responses — `api/agent.py`, `api/app.py`, `front/app.js`
Replace `agent.invoke()` with `agent.stream(..., stream_mode="updates")` behind a new
`POST /chat/stream` returning SSE. Keep `run_chat()` intact so `/chat` stays backward
compatible. Emit three event kinds: `tool_call`, `tool_result`, `token`. The frontend already
renders tool calls as collapsible widgets — stream into those same widgets instead of
appending after the fact.

### 3.2 Durable memory — `api/agent.py`, `pyproject.toml`
Swap `InMemorySaver` for `langgraph.checkpoint.sqlite.SqliteSaver` (or Postgres for
multi-worker). Keep it injectable so tests can pass an in-memory saver. This is a
**one-attribute change** with a large payoff: sessions survive `reload=True` restarts.

### 3.3 Self-correcting SQL — `api/tools/handlers.py`, `api/agent.py`
Today a malformed query raises and the turn dies. Instead, catch `mysql.connector.Error`
inside `execute_sql_tool` and return a structured
`{"error": "...", "hint": "...", "retryable": true}` payload. The ReAct loop will then see the
error as a `ToolMessage` and can repair the query itself. Cap repair attempts by tracking
consecutive failures so a loop cannot burn all 8 steps.
*Caution:* this intentionally relaxes the "never return exceptions in tool results" pitfall in
§15 of the instructions — only for this one controlled, typed error shape. Never leak
tracebacks or connection strings.

### 3.4 Token-aware trimming — `api/utils/trimmer.py`
`trim_messages()` currently counts messages and characters. Add a `count_tokens` function
(via `tiktoken` or `ChatOpenAI.get_num_tokens_from_messages`) and a `max_tokens` setting in
`CONTEXT_DEFAULTS`. Keep `trim_messages()` a **pure function** and keep all trimming logic in
this module — both are existing invariants.

### 3.5 Schema caching — `api/tools/handlers.py`
`get_schema_tool()` hits `information_schema` on every call, and the agent calls it at the
start of nearly every conversation. Add a TTL cache (default 300s) with an explicit
invalidation hook. This cuts first-turn latency noticeably.

### 3.6 Query guardrails — `api/tools/handlers.py`
Extend the SELECT guard with: a mandatory `LIMIT` injection when none is present, a
configurable `max_rows`, a statement timeout, and rejection of stacked statements (`;`).
The system prompt already tells the agent to "warn the user if a query might be slow" —
make that structurally enforced rather than advisory.

### 3.7 Observability — new `api/utils/telemetry.py`
Wrap `run_chat()` and `run_tool()` to record per-turn `{session_id, tool, duration_ms,
input_tokens, output_tokens, error}`. Expose `GET /metrics`. Without this you cannot tell
whether a prompt change made the agent better or merely slower.

### 3.8 Evaluation harness — new `tests/eval/`
A YAML corpus of `{question, expected_tools, assertion}` cases run against a test database.
This is the prerequisite for safely editing `SYSTEM_PROMPT` or `tools.json` descriptions —
both of which change agent behavior invisibly.

### 3.9 New tools worth adding
Follow §6 of the instructions file for each. Candidates, in value order:

| Tool | Purpose |
| --- | --- |
| `profile_column` | Distribution, cardinality, min/max, top-N values for a single column |
| `suggest_query` | Return candidate SQL **without** executing it (plan-then-confirm UX) |
| `explain_query` | `EXPLAIN` output so the agent can self-assess cost before running |
| `find_related_tables` | Join-path discovery between two tables via the FK graph |
| `compare_tables` | Row-count and schema drift between bronze/silver/gold counterparts |

---

## 4. Rules specific to agentic changes

These supplement — never replace — §10 Security and §15 Common Pitfalls in the instructions file.

- **Prompt changes are behavior changes.** Editing `SYSTEM_PROMPT` or any `description` in
  `tools.json` alters what the LLM does. Treat both as code: justify the change, and ideally
  cover it with an eval case (§3.8).
- **Keep `tools.json` and the handler signature in lockstep.** A drifted schema produces
  hallucinated arguments, which surface as confusing runtime errors far from the cause.
- **Do not raise `recursion_limit` to fix a runaway agent.** A loop means the tool returned an
  unusable result or its description is ambiguous. Fix the tool.
- **Never widen the SQL guard.** If a feature appears to need writes, add a separate,
  explicitly-gated tool with its own confirmation flow — do not loosen `execute_sql`.
- **Preserve the two-layer tool design.** `handlers.py` holds logic; `helpers.py` holds the
  LangChain wrapper. The `/tool` endpoint and the agent must keep sharing the same handler, so
  the Schema Explorer and Lineage Graph continue to work without an LLM.
- **Every new tool needs a demo-mode branch** in `front/app.js` (`executeToolDemo()`), or the
  offline UI silently breaks.
- **Prefer additive endpoints.** `/chat` is consumed by the existing frontend; add
  `/chat/stream` rather than changing its contract.

---

## 5. Definition of done

A feature is complete when all of the following hold:

1. `tools.json`, the handler, `dispatch_tool()`, `__init__.py` exports, the LangGraph wrapper,
   and the demo-mode branch are all updated (for new tools).
2. `python main.py` starts cleanly and `POST /ping` succeeds.
3. The new capability is exercised through `/chat` at least once — confirm the agent actually
   *chooses* the tool, not just that the tool works when called directly.
4. Context trimming still works on a long session (send >10 turns; confirm no context error).
5. Security controls are intact: SELECT-only guard, `validate_identifier()` on every
   interpolated identifier, no API key in any response payload.
6. `README.md` endpoint/feature lists are updated if the public surface changed.
