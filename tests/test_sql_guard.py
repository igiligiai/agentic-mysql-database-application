from api.utils.sql_guard import GuardRejection, GuardResult, validate_query


def _reject(sql: str) -> GuardRejection:
    result = validate_query(sql)
    assert isinstance(result, GuardRejection), f"expected rejection for {sql!r}"
    return result


def test_plain_select_is_allowed():
    result = validate_query("SELECT make FROM cars")
    assert isinstance(result, GuardResult)


def test_cte_select_is_allowed():
    result = validate_query("WITH x AS (SELECT 1 AS n) SELECT n FROM x")
    assert isinstance(result, GuardResult)


def test_union_select_is_allowed():
    result = validate_query("SELECT a FROM t UNION SELECT b FROM u")
    assert isinstance(result, GuardResult)


def test_information_schema_is_allowed():
    result = validate_query("SELECT TABLE_NAME FROM information_schema.TABLES")
    assert isinstance(result, GuardResult)


def test_stacked_statements_are_rejected():
    assert _reject("SELECT 1; DROP TABLE cars").code == "SQL_FORBIDDEN"


def test_delete_is_rejected():
    assert _reject("DELETE FROM cars").code == "SQL_FORBIDDEN"


def test_write_hidden_behind_a_cte_is_rejected():
    assert _reject("WITH x AS (SELECT 1) DELETE FROM cars").code == "SQL_FORBIDDEN"


def test_session_variable_assignment_is_rejected():
    assert _reject("SET @a = 1").code == "SQL_FORBIDDEN"


def test_grant_is_rejected():
    assert _reject("GRANT ALL ON *.* TO bob").code == "SQL_FORBIDDEN"


def test_sleep_is_rejected():
    assert _reject("SELECT SLEEP(5)").code == "SQL_FORBIDDEN"


def test_benchmark_is_rejected():
    assert _reject("SELECT BENCHMARK(100000, MD5('x'))").code == "SQL_FORBIDDEN"


def test_load_file_is_rejected():
    assert _reject("SELECT LOAD_FILE('/etc/passwd')").code == "SQL_FORBIDDEN"


def test_cross_database_reference_is_rejected():
    assert _reject("SELECT * FROM otherdb.cars").code == "SQL_FORBIDDEN"


def test_allowed_schema_qualifier_is_accepted():
    result = validate_query(
        "SELECT * FROM cars_db.cars", allowed_schemas=frozenset({"cars_db"})
    )
    assert isinstance(result, GuardResult)


def test_unparseable_sql_is_rejected_as_syntax():
    # Retryable: this is the agent's own mistake, not a policy violation.
    assert _reject("SELCT * FROM cars").code == "SQL_SYNTAX"


def test_select_into_outfile_never_executes():
    # sqlglot's MySQL dialect cannot parse INTO OUTFILE, so it is rejected as
    # a syntax error rather than a policy violation. Either way it never runs.
    assert _reject("SELECT * FROM cars INTO OUTFILE '/tmp/x'").code in {
        "SQL_SYNTAX",
        "SQL_FORBIDDEN",
    }


def test_empty_sql_is_rejected():
    assert _reject("   ").code == "SQL_SYNTAX"
