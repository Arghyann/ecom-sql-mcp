"""End-to-end demo over real stdio MCP: spawns server.py and calls every tool.

The SQL below is what Claude would write after reading the schema from get_database_schema.
Run:  python demo.py
"""
import asyncio
import json
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

QUESTIONS = {
    "Which product categories generate the most revenue? (excl. cancelled orders)": """
        SELECT p.category, ROUND(SUM(oi.line_total), 2) AS revenue, COUNT(DISTINCT oi.order_id) AS orders
        FROM order_items oi
        JOIN orders o ON o.order_id = oi.order_id AND o.status <> 'cancelled'
        JOIN products p ON p.product_id = oi.product_id
        GROUP BY p.category ORDER BY revenue DESC""",
    "What is the monthly revenue trend? (last 12 months in data, excl. cancelled)": """
        WITH m AS (
          SELECT SUBSTR(order_date, 1, 7) AS month, SUM(order_total) AS revenue, COUNT(*) AS orders
          FROM orders WHERE status <> 'cancelled' GROUP BY 1)
        SELECT * FROM (SELECT month, ROUND(revenue, 2) revenue, orders FROM m ORDER BY month DESC LIMIT 12) ORDER BY month""",
    "Which marketing channels generate the most conversions?": """
        SELECT channel, COUNT(*) AS sessions, SUM(CASE WHEN converted THEN 1 ELSE 0 END) AS conversions,
               ROUND(100.0 * AVG(CASE WHEN converted THEN 1 ELSE 0 END), 2) AS conversion_rate_pct
        FROM sessions GROUP BY channel ORDER BY conversions DESC""",
    "Which categories have the highest return rate? (returned item rows / item rows sold)": """
        WITH sold AS (
          SELECT p.category, COUNT(*) AS items_sold FROM order_items oi JOIN products p USING (product_id) GROUP BY 1),
        ret AS (
          SELECT p.category, COUNT(DISTINCT r.order_item_id) AS items_returned FROM returns r JOIN products p USING (product_id) GROUP BY 1)
        SELECT s.category, s.items_sold, r.items_returned, ROUND(100.0 * r.items_returned / s.items_sold, 2) AS return_rate_pct
        FROM sold s JOIN ret r USING (category) ORDER BY return_rate_pct DESC""",
    "What is the relationship between returns and support tickets?": """
        WITH returned AS (SELECT DISTINCT order_id FROM returns),
             tix AS (SELECT order_id, COUNT(*) AS tickets FROM support_tickets GROUP BY 1)
        SELECT CASE WHEN r.order_id IS NULL THEN 'no return' ELSE 'has return' END AS order_group,
               COUNT(*) AS orders, COUNT(t.order_id) AS orders_with_ticket,
               ROUND(100.0 * COUNT(t.order_id) / COUNT(*), 2) AS ticket_rate_pct
        FROM orders o LEFT JOIN returned r USING (order_id) LEFT JOIN tix t USING (order_id)
        GROUP BY 1 ORDER BY 1""",
}


def payload(res):
    if res.is_error:
        return {'ERROR': res.content[0].text}
    if getattr(res, "structured_content", None):
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(res.content[0].text)


def show(title, r):
    print(f"\n### {title}")
    if isinstance(r, dict) and "rows" in r:
        print(" | ".join(r["columns"]))
        for row in r["rows"]:
            print(" | ".join(str(v) for v in row))
        print(f"({r['row_count']} rows, {r['execution_time_ms']} ms)")
    else:
        print(json.dumps(r, indent=1)[:1500])


async def main():
    params = StdioServerParameters(command=sys.executable, args=["server.py"])
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            print("tools:", [t.name for t in (await s.list_tools()).tools])
            show("list_tables", payload(await s.call_tool("list_tables", {})))
            show("describe_table(returns)", payload(await s.call_tool("describe_table", {"table_name": "returns"})))
            sch = payload(await s.call_tool("get_database_schema", {}))
            print("\n### get_database_schema relationships:", *sch["relationships"], sep="\n  ")
            for q, sql in QUESTIONS.items():
                t = time.time()
                res = await s.call_tool("query_database", {"sql": sql})
                show(q, payload(res))
                print(f"[wall {time.time()-t:.1f}s]")
            bad = await s.call_tool("query_database", {"sql": "DROP TABLE orders"})
            print("\n### unsafe query ->", bad.is_error, bad.content[0].text)


asyncio.run(main())
