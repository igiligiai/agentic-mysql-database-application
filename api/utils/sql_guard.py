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
        # ``.name`` unwraps sqlglot's ``Identifier`` wrapper, so it matches
        # regardless of backtick-quoting (e.g. `` `sleep` `` still yields
        # "sleep") -- unlike ``str(function_node.this)``, which would include
        # the backticks and never match ``_FORBIDDEN_FUNCTIONS``.
        name = function_node.name.lower()
        if name in _FORBIDDEN_FUNCTIONS:
            return GuardRejection(
                "SQL_FORBIDDEN", f"The function {name.upper()} is not permitted."
            )

    # ``locks`` (FOR UPDATE / LOCK IN SHARE MODE) lives on the ``exp.Select``
    # node that carries it, not necessarily on the statement root: a UNION's
    # branches are themselves ``exp.Select`` nodes, and a subquery's inner
    # SELECT is reachable the same way. Checking only ``statement.args`` would
    # miss both, so every SELECT in the tree is inspected.
    if statement.args.get("locks") or any(
        select_node.args.get("locks") for select_node in statement.find_all(exp.Select)
    ):
        return GuardRejection(
            "SQL_FORBIDDEN",
            "Locking reads (FOR UPDATE / LOCK IN SHARE MODE) are not permitted.",
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
    """Guarantee the query cannot return more than ``max_rows`` rows.

    ``Expression.limit()`` replaces any existing LIMIT, so it is only called
    when the query has none or has one that is too large.
    """
    current = _existing_limit(statement)

    if current is None:
        limited = statement.limit(max_rows)
        return GuardResult(sql=limited.sql(dialect=DIALECT), limit_injected=True)

    if current > max_rows:
        limited = statement.limit(max_rows)
        return GuardResult(sql=limited.sql(dialect=DIALECT), limit_clamped=True)

    return GuardResult(sql=statement.sql(dialect=DIALECT))
