"""SQL safety validation built on SQLGlot's AST (not regex).

Policy: exactly one read-only statement (SELECT / UNION / WITH ... SELECT),
touching only the seven approved tables (or CTE names defined in the query),
calling no file/network/system functions. The SQL that is *executed* is the
SQLGlot-regenerated DuckDB SQL of the validated AST, so what we checked is what runs.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from config import ALLOWED_TABLES, MAX_SQL_CHARS


class SQLValidationError(ValueError):
    """Raised when SQL is rejected. The message is safe to show to the model."""


# Any of these node types anywhere in the tree => reject.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    t
    for t in (
        getattr(exp, n, None)
        for n in (
            "Insert", "Update", "Delete", "Drop", "Alter", "AlterTable", "Create", "Command",
            "Copy", "Merge", "Set", "Use", "Pragma", "Attach", "Detach", "Install", "Load",
            "Transaction", "Commit", "Rollback", "Grant", "Revoke", "Analyze", "TruncateTable",
            "Into", "Export", "Describe", "Show", "LoadData", "Kill", "Declare", "Refresh",
            "Lock", "LockingProperty",
        )
    )
    if t is not None
)

# Functions sqlglot doesn't model natively (parsed as exp.Anonymous) are rejected
# unless listed here. Natively-modelled functions (SUM, COUNT, DATE_TRUNC, ...) are fine.
_SAFE_ANONYMOUS_FUNCTIONS = frozenset(
    """
    abs ceil ceiling floor round trunc sign sqrt pow power exp ln log log10 log2 mod
    greatest least coalesce nullif ifnull typeof
    lower upper trim ltrim rtrim length strlen len left right concat concat_ws replace
    substr substring starts_with ends_with contains position instr strpos reverse
    regexp_matches regexp_replace regexp_extract split_part string_split lpad rpad
    date_trunc date_part date_diff date_add date_sub datediff dateadd datesub datepart
    year month day hour minute second quarter week dayofweek dayofmonth dayofyear
    monthname dayname epoch strftime strptime make_date make_timestamp
    last_day to_days to_months to_years age today now current_date current_timestamp
    approx_count_distinct bool_and bool_or count_if countif any_value arg_min arg_max
    argmin argmax median mode quantile quantile_cont quantile_disc stddev stddev_pop
    stddev_samp var_pop var_samp variance corr covar_pop covar_samp skewness kurtosis
    list array_agg string_agg list_agg histogram first last
    list_contains list_distinct list_sort list_aggregate array_length
    row_number rank dense_rank percent_rank cume_dist ntile lag lead first_value last_value nth_value
    try_cast cast
    """.split()
)

# Never allowed even if sqlglot models them (defence in depth).
_DENIED_FUNCTION_PREFIXES = (
    "read_", "parquet_", "duckdb_", "pragma_", "sniff_", "glob", "getenv", "current_setting",
    "query", "load", "install", "http", "write_", "copy", "iceberg", "delta_", "json_read",
    "csv_", "to_parquet", "to_csv", "enable_", "set_", "force_", "getvariable", "current_database",
    "current_schema", "current_user", "version", "pg_", "which_secret", "md5_file", "sha256_file",
)

_ROOT_TYPES = (exp.Select, exp.SetOperation) if hasattr(exp, "SetOperation") else (exp.Select, exp.Union)


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        name = node.name
    else:
        name = node.sql_name() if hasattr(node, "sql_name") else type(node).__name__
    return str(name).lower()


def validate_sql(sql: str, dialect: str = "duckdb") -> exp.Expression:
    """Validate and return the parsed AST. Raises SQLValidationError on any violation."""
    if not isinstance(sql, str) or not sql.strip():
        raise SQLValidationError("SQL is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise SQLValidationError(f"SQL is too long (max {MAX_SQL_CHARS} characters).")
    if "\x00" in sql:
        raise SQLValidationError("SQL contains a NUL byte.")

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except (ParseError, TokenError) as e:
        raise SQLValidationError(f"SQL could not be parsed: {str(e).splitlines()[0][:300]}") from e

    if not statements:
        raise SQLValidationError("SQL is empty.")
    if len(statements) != 1:
        raise SQLValidationError("Only a single SQL statement is allowed (found multiple).")

    tree = statements[0]
    if not isinstance(tree, _ROOT_TYPES):
        raise SQLValidationError(
            f"Only SELECT queries are allowed (got {type(tree).__name__.upper()})."
        )

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLValidationError(f"{type(node).__name__.upper()} is not allowed; read-only SELECT only.")

        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier):
                raise SQLValidationError(
                    "Table functions and file paths (e.g. read_parquet('...'), FROM 'file') are not allowed."
                )
            if node.args.get("db") or node.args.get("catalog"):
                raise SQLValidationError("Schema/catalog-qualified table names are not allowed; use the bare table name.")
            name = node.name.lower()
            if name not in ALLOWED_TABLES and name not in cte_names:
                raise SQLValidationError(
                    f"Table '{node.name}' is not available. Allowed tables: {', '.join(sorted(ALLOWED_TABLES))}."
                )

        elif isinstance(node, exp.Func):
            name = _function_name(node)
            if name.startswith(_DENIED_FUNCTION_PREFIXES) or name in {"query", "glob"}:
                raise SQLValidationError(f"Function '{name}' is not allowed.")
            if isinstance(node, exp.Anonymous) and name not in _SAFE_ANONYMOUS_FUNCTIONS:
                raise SQLValidationError(
                    f"Function '{name}' is not in the allowed function list."
                )

        elif isinstance(node, (exp.Placeholder, exp.Parameter)):
            raise SQLValidationError("Query parameters/variables are not allowed.")

    return tree


def to_safe_sql(sql: str) -> str:
    """Validate, then return DuckDB SQL regenerated from the validated AST."""
    return validate_sql(sql).sql(dialect="duckdb")
