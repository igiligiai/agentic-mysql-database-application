"""HTTP-boundary error-contract coverage for api/app.py.

/ping, /tool, and /chat must never forward str(exc) for a driver or generic
exception: api/utils/errors.py's redaction is the only path credentials,
hosts, or driver text may take toward a client.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from mysql.connector import Error as MySQLError

import api.app as app_module


def _client() -> TestClient:
    return TestClient(app_module.app)


def test_ping_redacts_a_driver_error(monkeypatch):
    def boom(*_args, **_kwargs):
        raise MySQLError(
            msg="Access denied for user 'root'@'10.0.0.5' (using password: YES)",
            errno=1045,
        )

    monkeypatch.setattr(app_module, "fetch_all", boom)

    response = _client().post("/ping")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "10.0.0.5" not in detail
    assert "root" not in detail


def test_tool_dispatch_redacts_a_driver_error(monkeypatch):
    def boom(_tool, _args):
        raise MySQLError(
            msg="Access denied for user 'root'@'10.0.0.5' (using password: YES)",
            errno=1045,
        )

    monkeypatch.setattr(app_module.agent, "run_tool", boom)

    response = _client().post("/tool", json={"tool": "get_schema", "args": {}})

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "10.0.0.5" not in detail
    assert "root" not in detail


def test_chat_never_forwards_a_bare_exception_message(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError(
            "connecting to mysql://root:hunter2@10.0.0.5:3306/cars failed"
        )

    monkeypatch.setattr(app_module.agent, "run_chat", boom)

    response = _client().post("/chat", json={"message": "hi"})

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "hunter2" not in detail
    assert "10.0.0.5" not in detail


def test_chat_redacts_a_value_error_instead_of_forwarding_it_verbatim(monkeypatch):
    # ValueError can originate anywhere inside agent.invoke() (langchain/openai
    # /json internals), so its text must go through the same redaction helper
    # as every other exception arm -- never str(exc) verbatim.
    def boom(*_args, **_kwargs):
        raise ValueError(
            "invalid config: mysql://root:hunter2@10.0.0.5:3306/cars"
        )

    monkeypatch.setattr(app_module.agent, "run_chat", boom)

    response = _client().post("/chat", json={"message": "hi"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "hunter2" not in detail
    assert "10.0.0.5" not in detail
