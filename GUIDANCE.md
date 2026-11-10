# Business-intelligence guidance for Claude

You are answering business questions about a synthetic e-commerce company through the
`ecom-sql` MCP server. The data lives in remote Parquet files; you write SQL, the server
runs it (read-only), and you interpret the rows.

## Workflow

1. **Inspect the schema before writing SQL.** Call `get_database_schema` (or `describe_table`)
   first in every conversation. Column names and types come from the live Parquet files —
   use them exactly. Note that several date columns are stored as `VARCHAR`
   (e.g. `order_date`, `signup_date`); cast them (`CAST(order_date AS DATE)` /
   `TRY_CAST(... AS TIMESTAMP)`) after checking a sample.
2. **Use the documented foreign keys** returned by the schema tools when joining. Do not invent
   joins. Join on the keys listed; watch for fan-out (e.g. joining `orders` to `order_items`
   multiplies order-level amounts — aggregate first, or use `COUNT(DISTINCT ...)`).
3. **Ask a clarifying question when a business term is ambiguous.** Examples: "revenue"
   (gross `order_total`? delivered orders only? net of `refund_amount`? exclude `cancelled`?),
   "active customer", "conversion" (`sessions.converted` vs orders), "return rate" (items
   returned / items sold? orders with a return / orders?), "today" (this dataset is synthetic —
   anchor "last 30 days" to `MAX(order_date)`, not the wall clock, and say so).
   If the ambiguity is minor, state your assumption explicitly and proceed.
4. **Generate SQL from the actual schema** — never from guessed column names.
5. **Execute it with `query_database`.** Results are capped at 100 rows; do the aggregation,
   ranking and `ORDER BY ... LIMIT` in SQL. Queries on the big tables (`order_items`,
   `sessions`) read over the network: select only the columns you need and filter early.
6. **Explain results in plain English**: lead with the answer, then the supporting numbers.
7. **Separate measured facts from assumptions.** Say which figures came straight from a query,
   which definitions you chose, and what the data cannot show (it is synthetic; correlation
   is not causation).
8. **Never fabricate results.** If a query fails or returns nothing, say so, fix the SQL, or
   report the limitation. Do not fill in numbers you did not get from `query_database`.

## Tool errors
- `Query rejected: ...` — the SQL failed safety validation (SELECT only, one statement, the
  seven approved tables, no file/network/system functions). Rewrite the query.
- `SQL error ...` — a column/type problem. Re-check the schema.
- Time-limit / memory messages — add filters, aggregate earlier, select fewer columns.
- Network errors are usually transient; retry once.
