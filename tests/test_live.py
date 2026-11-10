"""Live integration tests against Hugging Face. Run with: RUN_LIVE=1 pytest tests/test_live.py"""
import os

import pytest

import server
from mcp.server.mcpserver.exceptions import ToolError
from database import QueryError, db

pytestmark = pytest.mark.skipif(not os.getenv("RUN_LIVE"), reason="set RUN_LIVE=1 to hit Hugging Face")


def test_list_tables():
    assert {t["table"] for t in server.list_tables()} == set(server.config.ALLOWED_TABLES)


def test_describe_table():
    d = server.describe_table("orders")
    assert any(c["name"] == "order_id" for c in d["columns"])
    assert d["primary_key"] == ["order_id"]
    assert {"customer_id", "session_id"} <= {f["column"] for f in d["foreign_keys"]}
    assert len(d["sample_rows"]["rows"]) == 3


def test_schema():
    s = server.get_database_schema()
    assert len(s["tables"]) == 7
    assert "order_items.order_id -> orders.order_id" in s["relationships"]


def test_query_and_row_cap():
    r = server.query_database("SELECT product_id FROM products")
    assert r["row_count"] == 100 and r["truncated"] is True
    assert r["columns"] == ["product_id"] and "execution_time_ms" in r


def test_join_cte_aggregate():
    r = server.query_database(
        "WITH x AS (SELECT product_id, COUNT(*) n FROM returns GROUP BY 1) "
        "SELECT p.category, SUM(x.n) FROM x JOIN products p USING (product_id) GROUP BY 1 ORDER BY 2 DESC"
    )
    assert r["row_count"] > 0


def test_rejections_via_tool():
    for bad in ["DROP TABLE orders", "SELECT 1; SELECT 2", "SELECT * FROM read_csv('/etc/passwd')"]:
        with pytest.raises(ToolError, match="rejected"):
            server.query_database(bad)


def test_engine_level_lockdown():
    # Even bypassing the validator, DuckDB itself refuses these once configured.
    db.warm_up()
    for stmt in ["SELECT * FROM read_csv('/etc/passwd')", "INSTALL httpfs", "SET memory_limit='99GB'"]:
        with pytest.raises(Exception):
            db._con.execute(stmt)


def test_timeout(monkeypatch):
    monkeypatch.setattr(server.config, "QUERY_TIMEOUT_S", 0.05)
    import database
    monkeypatch.setattr(database.config, "QUERY_TIMEOUT_S", 0.05)
    with pytest.raises(ToolError, match="time limit"):
        server.query_database("SELECT COUNT(*) FROM order_items a JOIN order_items b ON a.product_id = b.product_id")
