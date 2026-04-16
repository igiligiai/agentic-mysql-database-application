# agentic-mysql-database-application

Python-first agentic MySQL application with two main folders:

- api: FastAPI backend and database tools
- front: static frontend implementation

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
	- get_schema
	- get_table_lineage
	- describe_table
	- check_data_quality
- POST /chat for Python-side OpenAI chat + tool-calling orchestration
- SELECT-only SQL execution guard for execute_sql
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
- OPENAI_API_KEY (required unless passed from frontend as optional override)

OpenAI tool-calling initialization happens in Python backend (`api/agent.py`).
Frontend key input is optional and acts as per-session override for `/chat`.

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

- Legacy files autobase_server.js and car_data_agent.html are preserved as migration references.
- Frontend defaults to backend URL http://localhost:8000.
