"""ecom-sql: an MCP server that lets Claude query a remote e-commerce dataset with SQL.

Claude Desktop -> (stdio) -> this server -> DuckDB (httpfs) -> Hugging Face Parquet files.
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import config
from database import QueryError, db

# stdout is the MCP transport; all logging goes to stderr.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_GUIDANCE = (Path(__file__).parent / "GUIDANCE.md").read_text(encoding="utf-8")
mcp = MCPServer("ecom-sql", instructions=_GUIDANCE)


def _rels(table: str) -> list[dict]:
    return [
        {"column": c, "references_table": t, "references_column": rc}
        for c, t, rc in config.TABLES[table]["foreign_keys"]
    ]


def _check(table_name: str) -> str:
    name = (table_name or "").strip().lower()
    if name not in config.ALLOWED_TABLES:
        raise ToolError(f"Unknown table '{table_name}'. Available: {', '.join(config.TABLES)}")
    return name


@mcp.tool()
def list_tables() -> list[dict]:
    """List the available tables with a short description of each."""
    return [{"table": t, "description": m["description"]} for t, m in config.TABLES.items()]


@mcp.tool()
def describe_table(table_name: str) -> dict:
    """Describe one table: live column names/types, 3 sample rows, primary key and foreign keys."""
    t = _check(table_name)
    try:
        cols = db.table_columns(t)
        sample = db.sample_rows(t, 3)
    except QueryError as e:
        raise ToolError(str(e)) from e
    meta = config.TABLES[t]
    return {
        "table": t,
        "description": meta["description"],
        "columns": cols,
        "primary_key": meta["primary_key"],
        "foreign_keys": _rels(t),
        "sample_rows": {"columns": sample["columns"], "rows": sample["rows"]},
    }


@mcp.tool()
def get_database_schema() -> dict:
    """Consolidated schema for all seven tables (live columns/types, keys, relationships)."""
    tables, relationships = {}, []
    for t, meta in config.TABLES.items():
        try:
            cols = db.table_columns(t)
        except QueryError as e:
            raise ToolError(str(e)) from e
        tables[t] = {
            "description": meta["description"],
            "columns": [f"{c['name']} {c['type']}" for c in cols],
            "primary_key": meta["primary_key"],
        }
        relationships += [
            f"{t}.{c} -> {rt}.{rc}" for c, rt, rc in meta["foreign_keys"]
        ]
    return {
        "tables": tables,
        "relationships": relationships,
        "notes": [
            "Keys/relationships come from the dataset README (columns marked FK); primary keys are the "
            "leading *_id column of each table.",
            "Many date columns are VARCHAR in the Parquet files - CAST before date arithmetic.",
            "Query results are capped at %d rows." % config.MAX_ROWS,
        ],
    }


@mcp.tool()
def query_database(sql: str) -> dict:
    """Run ONE read-only SELECT (joins, aggregations, CTEs, subqueries allowed) against the seven tables.

    Returns {columns, rows, row_count, truncated, execution_time_ms}. At most 100 rows are returned,
    so aggregate / ORDER BY / LIMIT in SQL. Mutations, multiple statements, other tables, file access
    and network functions are rejected.
    """
    try:
        return db.query(sql)
    except QueryError as e:
        raise ToolError(str(e)) from e


if __name__ == "__main__":
    threading.Thread(target=db.warm_up, daemon=True).start()
    mcp.run(transport="stdio")
