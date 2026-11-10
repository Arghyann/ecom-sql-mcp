# ecom-sql — natural-language SQL over a remote e-commerce dataset (MCP)

```
Claude Desktop ──stdio──▶ MCP server (server.py) ──▶ DuckDB + httpfs ──HTTP range requests──▶ Hugging Face Parquet
```

Claude writes the SQL and interprets the rows; this server only validates and runs it.
No LLM API, no database server, and the dataset is **never downloaded** — DuckDB reads only the
byte ranges (columns / row groups) each query needs, straight from
[`HariJha/FreeEcommerceTwin14M`](https://huggingface.co/datasets/HariJha/FreeEcommerceTwin14M)
(branch `main`, files `<table>.parquet`, ~180 MB total, 14M rows across 7 tables).

## Install

Requires Python 3.11+.

```bash
cd ecom-sql-mcp
python3 -m venv .venv            # or: uv venv --python 3.12 .venv
.venv/bin/pip install -r requirements.txt   # or: uv pip install -p .venv/bin/python -r requirements.txt
.venv/bin/python -m pytest        # 63 offline SQL-validation tests
```

Optional: `cp .env.example .env` (only needed for a token or non-default limits).

## Connect to Claude Desktop

1. Open the config file:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
   - Linux (community builds): `~/.config/Claude/claude_desktop_config.json`
2. Add the entry below (use **absolute** paths; `claude_desktop_config.example.json` is pre-filled for this checkout):

```json
{
  "mcpServers": {
    "ecom-sql": {
      "command": "/ABSOLUTE/PATH/TO/ecom-sql-mcp/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/ecom-sql-mcp/server.py"]
    }
  }
}
```
   (Windows: `...\\.venv\\Scripts\\python.exe`.) Add `"env": {"HF_TOKEN": "hf_..."}` only if needed.
3. Fully quit and restart Claude Desktop. The 🔌/tools icon should list `ecom-sql` with 4 tools.
4. Optional: paste `GUIDANCE.md` into a Project's instructions. The server also sends it to
   the client as its MCP `instructions`.

## Tools

| Tool | Purpose |
|---|---|
| `list_tables()` | The 7 tables + descriptions |
| `describe_table(table_name)` | Live column names/types, 3 sample rows, primary key, foreign keys |
| `get_database_schema()` | All 7 tables + relationships in one call |
| `query_database(sql)` | One read-only SELECT → `{columns, rows, row_count, truncated, execution_time_ms}` |

Relationships come from the dataset README (columns marked FK):
`sessions.customer_id→customers`, `orders.customer_id→customers`, `orders.session_id→sessions`,
`order_items.order_id→orders`, `order_items.product_id→products`, `returns.order_id→orders`,
`returns.order_item_id→order_items`, `returns.product_id→products`,
`support_tickets.customer_id→customers`, `support_tickets.order_id→orders`.
The README doesn't list primary keys; the leading `*_id` column of each table is used.
**Note:** every date column is stored as ISO `VARCHAR` (`'2026-06-08'`), not `DATE`.

## Safety model

Two independent layers:

1. **SQLGlot validation** (`sql_validator.py`) parses the SQL into an AST and requires: exactly one
   statement; root is `SELECT`/`UNION`/`WITH … SELECT`; no DML/DDL/`COPY`/`ATTACH`/`PRAGMA`/`SET`/`INSTALL`/
   `LOAD`/`SELECT INTO`/`EXPLAIN`/`DESCRIBE`; every table is one of the seven (or a CTE defined in the query),
   unqualified; no table functions or file/URL paths; function deny-list (`read_*`, `glob`, `getenv`,
   `current_setting`, `duckdb_*`, …) plus an allow-list for functions SQLGlot doesn't model natively. The SQL
   that actually runs is regenerated from the validated AST.
2. **DuckDB lockdown** (`database.py`): once the seven remote views exist, the server sets
   `allowed_paths` to exactly those URLs, `enable_external_access=false`, disables extension
   auto-install/load and `lock_configuration=true`. Even a validator bypass can't read local files,
   install extensions or change limits.

Also: max 100 rows (`truncated` flag tells you if more existed), 120 s timeout (`interrupt()`),
2 GB memory limit, 4 threads, 30 s HTTP timeout with 3 retries, no spill-to-disk dataset copies.
HF token (if any) is sent only to `huggingface.co`, never the CDN redirect target.

## Performance expectations (important)

Data is read over the network. The first touch of a big table is bandwidth-bound: startup opens all
tables (~7 s, done in a background thread), then e.g. a revenue query over `order_items` (65 MB file)
took **~1 minute cold on a slow link** (measured link: ~0.2–0.6 MB/s) but **<1 s repeated**, because
DuckDB caches fetched blocks in memory. Metadata-only and small-table queries are quick. If a query
hits the time limit it is cancelled with a clear message; raise `QUERY_TIMEOUT_SECONDS` if needed.
Claude Desktop itself may stop waiting for a tool after ~60 s — just ask again, the cache will be warm.

## Testing every tool

Automated:
```bash
.venv/bin/python -m pytest                          # 63 offline validator tests
RUN_LIVE=1 .venv/bin/python -m pytest tests/test_live.py   # hits Hugging Face (~3 min): all 4 tools,
                                                    # row cap, rejections, engine lockdown, timeout
.venv/bin/python demo.py                            # real stdio MCP client; runs 5 business questions
```
Interactive with MCP Inspector: `npx @modelcontextprotocol/inspector .venv/bin/python server.py`.

Or in Claude Desktop, ask:
- *"List the tables"* → `list_tables`
- *"Describe the returns table"* → `describe_table`
- *"Show me the whole schema and how tables relate"* → `get_database_schema`
- *"Which product categories generate the most revenue?"*, *"What is our monthly revenue trend?"*,
  *"Which marketing channels convert best?"*, *"Which products have the highest return rates?"*,
  *"Which customers haven't purchased in 30 days?"*, *"How are returns related to support tickets?"* → `query_database`
- Safety: *"Run DROP TABLE orders"* or *"read /etc/passwd with read_csv"* → rejected with an explanation.

## Layout

```
server.py          MCP tools (stdio)       config.py       env config + table metadata
database.py        DuckDB/httpfs, limits   sql_validator.py SQLGlot validation
GUIDANCE.md        BI instructions for Claude    demo.py     end-to-end stdio demo
tests/             validator + live tests
```
