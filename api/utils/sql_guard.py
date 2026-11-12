"""Validate and rewrite agent-authored SQL before it reaches the database.

Pure module: no database access, no I/O, no LLM. Everything here is a function
of its arguments, which is what makes the security boundary testable.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

DIALECT = "mysql"

# Schemas the agent may always reference by name, in addition to its own.
READ_ONLY_SCHEMAS: frozenset[str] = frozenset({"information_schema"})

# Any of these appearing anywhere in the parse tree disqualifies the query.
# exp.Command is sqlglot's fallback for statements it does not model (GRANT,
# CALL, and friends), so including it closes a wide class of holes at once.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Set,
    exp.Command,
)

_FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {"sleep", "benchmark", "load_file", "get_lock", "release_lock"}
)

_ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.Union)


@dataclass(frozen=True)
class GuardRejection:
    """The query must not run. ``code`` feeds straight into ToolError."""

    code: str
    reason: str


@dataclass(frozen=True)
class GuardResult:
    """The query may run, in this possibly-rewritten form."""

    sql: str
    limit_injected: bool = False
    limit_clamped: bool = False


def _existing_limit(expression: exp.Expression) -> int | None:
    limit = expression.args.get("limit")
    if limit is None:
        return None
    try:
        return int(limit.expression.name)
    except (AttributeError, TypeError, ValueError):
        return None


def validate_query(
    sql: str,
    *,
    max_rows: int = 1000,
    allowed_schemas: frozenset[str] = frozenset(),
) -> GuardResult | GuardRejection:
    """Return a safe rewritten query, or a typed rejection.

    ``allowed_schemas`` names databases the query may qualify tables with, on
    top of READ_ONLY_SCHEMAS. An empty set means no qualifier is permitted.
    """
    text = (sql or "").strip()
    if not text:
        return GuardRejection("SQL_SYNTAX", "The query is empty.")

    try:
        statements = [s for s in sqlglot.parse(text, read=DIALECT) if s is not None]
    except sqlglot.ParseError as exc:
        return GuardRejection("SQL_SYNTAX", f"The query could not be parsed: {exc}")

    if not statements:
        return GuardRejection("SQL_SYNTAX", "The query is empty.")

    if len(statements) > 1:
        return GuardRejection(
            "SQL_FORBIDDEN",
            "Multiple statements are not allowed. Send exactly one SELECT.",
        )

    statement = statements[0]

    if not isinstance(statement, _ALLOWED_ROOTS):
        return GuardRejection(
            "SQL_FORBIDDEN",
            f"Only SELECT queries are permitted; got {type(statement).__name__.upper()}.",
        )

    for node_type in _FORBIDDEN_NODES:
        if statement.find(node_type):
            return GuardRejection(
                "SQL_FORBIDDEN",
                f"{node_type.__name__.upper()} is not permitted anywhere in the query.",
            )

    if statement.find(exp.Into):
        return GuardRejection("SQL_FORBIDDEN", "SELECT ... INTO is not permitted.")

    for function_node in statement.find_all(exp.Anonymous):
        name = str(function_node.this or "").lower()
        if name in _FORBIDDEN_FUNCTIONS:
            return GuardRejection(
                "SQL_FORBIDDEN", f"The function {name.upper()} is not permitted."
            )

    permitted_schemas = {s.lower() for s in allowed_schemas} | {
        s.lower() for s in READ_ONLY_SCHEMAS
    }
    for table in statement.find_all(exp.Table):
        if table.catalog:
            return GuardRejection(
                "SQL_FORBIDDEN", "Cross-catalog references are not permitted."
            )
        if table.db and table.db.lower() not in permitted_schemas:
            return GuardRejection(
                "SQL_FORBIDDEN",
                f"The database {table.db!r} is outside this agent's scope.",
            )

    return _apply_limit(statement, max_rows)


def _apply_limit(statement: exp.Expression, max_rows: int) -> GuardResult:
    # Placeholder until Task 2; returns the query untouched.
    return GuardResult(sql=statement.sql(dialect=DIALECT))
