import pytest

from sql_validator import SQLValidationError, to_safe_sql, validate_sql

ACCEPT = [
    "SELECT 1",
    "SELECT * FROM orders",
    "select status, count(*) from orders group by status order by 2 desc",
    "WITH m AS (SELECT date_trunc('month', CAST(order_date AS DATE)) mth, SUM(order_total) rev FROM orders GROUP BY 1) SELECT * FROM m ORDER BY mth",
    "SELECT o.order_id FROM orders o JOIN customers c ON c.customer_id = o.customer_id LEFT JOIN returns r ON r.order_id = o.order_id",
    "SELECT * FROM orders WHERE order_id IN (SELECT order_id FROM returns)",
    "SELECT * FROM (SELECT product_id FROM products) p",
    "SELECT order_id FROM orders UNION ALL SELECT order_id FROM returns",
    "SELECT customer_id, ROW_NUMBER() OVER (PARTITION BY region ORDER BY lifetime_spend DESC) FROM customers",
    "SELECT quantile_cont(order_total, 0.5), median(order_total) FROM orders",
    "SELECT strftime(CAST(order_date AS DATE), '%Y-%m') FROM orders",
    "SELECT 1;",  # trailing semicolon is a single statement
    "SELECT 'drop table orders'",  # keyword inside a string literal is fine
    "-- a comment\nSELECT COUNT(*) FROM sessions WHERE converted",
]

REJECT = {
    "insert": "INSERT INTO orders VALUES (1)",
    "update": "UPDATE orders SET status='x'",
    "delete": "DELETE FROM orders",
    "drop": "DROP TABLE orders",
    "alter": "ALTER TABLE orders ADD COLUMN x INT",
    "create": "CREATE TABLE t AS SELECT * FROM orders",
    "create_view": "CREATE VIEW v AS SELECT 1",
    "truncate": "TRUNCATE orders",
    "copy_out": "COPY orders TO '/tmp/x.csv'",
    "copy_in": "COPY orders FROM '/tmp/x.csv'",
    "attach": "ATTACH '/tmp/x.db'",
    "install": "INSTALL httpfs",
    "load": "LOAD httpfs",
    "pragma": "PRAGMA database_list",
    "set": "SET memory_limit='100GB'",
    "call": "CALL pragma_version()",
    "multi": "SELECT 1; SELECT 2",
    "multi_mutation": "SELECT 1; DROP TABLE orders",
    "select_into": "SELECT * INTO x FROM orders",
    "unknown_table": "SELECT * FROM users",
    "info_schema": "SELECT * FROM information_schema.tables",
    "duckdb_tables": "SELECT * FROM duckdb_tables()",
    "qualified": "SELECT * FROM main.orders",
    "read_parquet": "SELECT * FROM read_parquet('https://evil.example/x.parquet')",
    "read_csv": "SELECT * FROM read_csv('/etc/passwd')",
    "read_text": "SELECT * FROM read_text('/etc/passwd')",
    "glob": "SELECT * FROM glob('/*')",
    "file_literal": "SELECT * FROM '/etc/passwd'",
    "url_literal": "SELECT * FROM 'https://evil.example/a.parquet'",
    "quoted_path": 'SELECT * FROM "/etc/passwd"',
    "getenv": "SELECT getenv('HOME')",
    "current_setting": "SELECT current_setting('memory_limit')",
    "range": "SELECT * FROM range(1000000000)",
    "generate_series": "SELECT * FROM generate_series(1, 1000000000)",
    "subquery_file": "SELECT * FROM orders WHERE order_id IN (SELECT column0 FROM read_csv('/etc/passwd'))",
    "cte_file": "WITH x AS (SELECT * FROM read_csv('/etc/passwd')) SELECT * FROM x",
    "cte_dml": "WITH x AS (SELECT 1) DELETE FROM orders",
    "unknown_function": "SELECT some_unknown_func(1)",
    "empty": "",
    "whitespace": "   ",
    "comment_only": "-- nothing",
    "garbage": "SELEC FROM WHERE ((",
    "explain": "EXPLAIN SELECT * FROM orders",
    "describe": "DESCRIBE orders",
    "show": "SHOW TABLES",
}


@pytest.mark.parametrize("sql", ACCEPT)
def test_accepts_safe_selects(sql):
    validate_sql(sql)


@pytest.mark.parametrize("name", REJECT)
def test_rejects_unsafe(name):
    with pytest.raises(SQLValidationError):
        validate_sql(REJECT[name])


def test_too_long():
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT " + "1," * 20000 + "1")


def test_non_string():
    with pytest.raises(SQLValidationError):
        validate_sql(None)  # type: ignore[arg-type]


def test_cte_may_shadow_nothing_but_be_referenced():
    # A CTE name that is not an approved table is OK as long as it is defined in the query.
    assert to_safe_sql("WITH tmp AS (SELECT 1 a) SELECT a FROM tmp")
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT * FROM tmp")


def test_executed_sql_is_regenerated_from_ast():
    out = to_safe_sql("select   1  as x -- hi")
    assert "--" not in out
