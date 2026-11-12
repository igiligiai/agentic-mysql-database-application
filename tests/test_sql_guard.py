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


def test_backtick_quoted_sleep_is_rejected():
    assert _reject("SELECT `sleep`(5)").code == "SQL_FORBIDDEN"


def test_backtick_quoted_benchmark_is_rejected():
    assert _reject("SELECT `BENCHMARK`(10, MD5('a'))").code == "SQL_FORBIDDEN"


def test_backtick_quoted_load_file_is_rejected():
    assert _reject("SELECT `LOAD_FILE`('/etc/hostname')").code == "SQL_FORBIDDEN"


def test_backtick_quoted_get_lock_is_rejected():
    assert _reject("SELECT `GET_LOCK`('lockname', 10)").code == "SQL_FORBIDDEN"


def test_backtick_quoted_release_lock_is_rejected():
    assert _reject("SELECT `RELEASE_LOCK`('lockname')").code == "SQL_FORBIDDEN"


def test_mixed_case_backtick_sleep_is_rejected():
    assert _reject("SELECT `Sleep`(5)").code == "SQL_FORBIDDEN"


def test_select_for_update_is_rejected():
    assert _reject("SELECT * FROM cars FOR UPDATE").code == "SQL_FORBIDDEN"


def test_select_lock_in_share_mode_is_rejected():
    assert _reject("SELECT * FROM cars LOCK IN SHARE MODE").code == "SQL_FORBIDDEN"


def test_select_for_update_inside_a_union_branch_is_rejected():
    assert _reject(
        "SELECT 1 UNION SELECT * FROM t FOR UPDATE"
    ).code == "SQL_FORBIDDEN"


def test_select_lock_in_share_mode_inside_a_union_branch_is_rejected():
    assert _reject(
        "SELECT 1 UNION SELECT * FROM t LOCK IN SHARE MODE"
    ).code == "SQL_FORBIDDEN"


def test_select_for_update_inside_a_subquery_is_rejected():
    assert _reject(
        "SELECT * FROM (SELECT * FROM t FOR UPDATE) x"
    ).code == "SQL_FORBIDDEN"


def test_plain_union_without_locking_is_still_allowed():
    result = validate_query("SELECT a FROM t UNION SELECT b FROM u")
    assert isinstance(result, GuardResult)


def test_ordinary_select_with_price_filter_is_allowed():
    result = validate_query("SELECT * FROM cars WHERE price > 100")
    assert isinstance(result, GuardResult)


def test_ordinary_select_with_division_is_allowed():
    result = validate_query("SELECT a/b/c FROM t")
    assert isinstance(result, GuardResult)


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


def test_missing_limit_is_injected():
    result = validate_query("SELECT make FROM cars", max_rows=1000)
    assert isinstance(result, GuardResult)
    assert result.limit_injected is True
    assert result.limit_clamped is False
    assert result.sql.endswith("LIMIT 1000")


def test_oversized_limit_is_clamped():
    result = validate_query("SELECT make FROM cars LIMIT 5000", max_rows=1000)
    assert isinstance(result, GuardResult)
    assert result.limit_clamped is True
    assert result.limit_injected is False
    assert result.sql.endswith("LIMIT 1000")


def test_small_limit_is_left_alone():
    result = validate_query("SELECT make FROM cars LIMIT 10", max_rows=1000)
    assert isinstance(result, GuardResult)
    assert result.limit_injected is False
    assert result.limit_clamped is False
    assert result.sql.endswith("LIMIT 10")


def test_limit_is_injected_on_a_union():
    result = validate_query("SELECT a FROM t UNION SELECT b FROM u", max_rows=50)
    assert isinstance(result, GuardResult)
    assert result.limit_injected is True
    assert result.sql.endswith("LIMIT 50")


def test_limit_is_injected_on_a_cte_query():
    result = validate_query("WITH x AS (SELECT 1 AS n) SELECT n FROM x", max_rows=25)
    assert isinstance(result, GuardResult)
    assert result.limit_injected is True
    assert result.sql.endswith("LIMIT 25")
