from mysql.connector import Error as MySQLError

from api.utils.errors import (
    from_mysql_error,
    is_tool_error,
    tool_error,
)


def test_tool_error_has_the_canonical_shape():
    err = tool_error("SQL_SYNTAX", detail="bad token", hint="fix it")
    assert set(err) == {"error", "hint", "retryable", "code"}
    assert err["code"] == "SQL_SYNTAX"
    assert err["retryable"] is True
    assert "bad token" in err["error"]
    assert err["hint"] == "fix it"


def test_forbidden_is_not_retryable():
    assert tool_error("SQL_FORBIDDEN", detail="writes blocked")["retryable"] is False


def test_unknown_code_falls_back_to_non_retryable():
    err = tool_error("NOT_A_REAL_CODE")
    assert err["code"] == "SQL_FAILED"
    assert err["retryable"] is False


def test_is_tool_error_recognises_the_shape():
    assert is_tool_error(tool_error("SQL_TIMEOUT")) is True
    assert is_tool_error({"rows": []}) is False
    assert is_tool_error("nope") is False


def test_parse_error_maps_to_syntax_and_forwards_the_message():
    err = from_mysql_error(MySQLError(msg="You have an error in your SQL syntax", errno=1064))
    assert err["code"] == "SQL_SYNTAX"
    assert err["retryable"] is True
    assert "syntax" in err["error"].lower()


def test_unknown_table_maps_and_hints_at_get_schema():
    err = from_mysql_error(MySQLError(msg="Table 'cars_db.nope' doesn't exist", errno=1146))
    assert err["code"] == "SQL_UNKNOWN_TABLE"
    assert "get_schema" in err["hint"]


def test_unknown_column_maps_and_hints_at_describe_table():
    err = from_mysql_error(MySQLError(msg="Unknown column 'nope' in 'field list'", errno=1054))
    assert err["code"] == "SQL_UNKNOWN_COLUMN"
    assert "describe_table" in err["hint"]


def test_timeout_maps_and_is_retryable():
    err = from_mysql_error(MySQLError(msg="Query execution was interrupted", errno=3024))
    assert err["code"] == "SQL_TIMEOUT"
    assert err["retryable"] is True


def test_connection_failure_maps_to_unavailable_and_is_not_retryable():
    err = from_mysql_error(MySQLError(msg="Can't connect to MySQL server", errno=2003))
    assert err["code"] == "SQL_UNAVAILABLE"
    assert err["retryable"] is False


def test_unmapped_errno_falls_back_to_sql_failed():
    err = from_mysql_error(MySQLError(msg="something odd", errno=9999))
    assert err["code"] == "SQL_FAILED"


def test_credentials_never_leak_into_the_error():
    exc = MySQLError(
        msg="Access denied for user 'root'@'localhost' (using password: hunter2)",
        errno=1045,
    )
    err = from_mysql_error(exc)
    blob = f"{err['error']} {err['hint']}"
    assert "hunter2" not in blob
    assert "root" not in blob
    assert "localhost" not in blob


def test_tracebacks_never_leak_into_the_error():
    err = from_mysql_error(RuntimeError('File "/app/api/utils/db.py", line 42, in fetch_matrix'))
    assert "db.py" not in err["error"]
    assert err["code"] == "SQL_FAILED"


def test_connection_string_uris_never_leak_into_the_error():
    err = tool_error("SQL_SYNTAX", detail="near mysql://root:hunter2@10.0.0.5:3306/cars_db")
    assert "hunter2" not in err["error"]
    assert "10.0.0.5" not in err["error"]
    assert "root" not in err["error"]


def test_bare_or_quoted_hostnames_never_leak_into_the_error():
    err = tool_error("SQL_SYNTAX", detail="Host '10.0.0.5' is not allowed to connect")
    assert "10.0.0.5" not in err["error"]


def test_filesystem_paths_without_py_suffix_never_leak_into_the_error():
    err = tool_error("TOOL_BAD_ARGS", detail="cannot read /etc/passwd")
    assert "/etc/passwd" not in err["error"]

    err_win = tool_error(
        "TOOL_BAD_ARGS", detail=r"cannot read C:\Users\Administrator\AppData\mysql.sock"
    )
    assert "mysql.sock" not in err_win["error"]


def test_alternate_password_spelling_never_leaks_into_the_error():
    err = tool_error("SQL_FORBIDDEN", detail="connect failed pwd=Sup3rSecret!")
    assert "Sup3rSecret!" not in err["error"]


def test_dns_hostnames_never_leak_into_the_error():
    err = tool_error(
        "SQL_SYNTAX", detail="Host 'db-primary.internal.example.com' is not allowed to connect"
    )
    assert "db-primary.internal.example.com" not in err["error"]


def test_sql_arithmetic_division_survives_redaction():
    err = tool_error("SQL_SYNTAX", detail="WHERE a/b/c = 1")
    assert "a/b/c" in err["error"]


def test_unknown_column_detail_survives_redaction():
    err = tool_error("SQL_UNKNOWN_COLUMN", detail="Unknown column 'cars.price' in 'field list'")
    assert "cars.price" in err["error"]


def test_unknown_table_detail_survives_redaction():
    err = tool_error("SQL_UNKNOWN_TABLE", detail="Table 'cars_db.silver_cars' doesn't exist")
    assert "cars_db.silver_cars" in err["error"]
