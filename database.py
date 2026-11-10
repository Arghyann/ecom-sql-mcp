"""DuckDB access to the remote Hugging Face Parquet files (no local copy of the data).

Each approved table is exposed as a VIEW over `read_parquet('<https url>')`. DuckDB's
httpfs extension fetches only the byte ranges it needs (column chunks / row groups),
so nothing is downloaded or materialised in full.
"""
from __future__ import annotations

import datetime as dt
import decimal
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any

import duckdb

import config
from sql_validator import SQLValidationError, to_safe_sql
import sqlglot
from sqlglot import exp

log = logging.getLogger("ecom-sql")


class QueryError(Exception):
    """A user-facing execution error (message is safe to return to the model)."""


def table_url(table: str) -> str:
    """Location of a table's Parquet file: a local path in local mode, else the HF URL."""
    if config.LOCAL_DATA_DIR is not None:
        return str(config.LOCAL_DATA_DIR / f"{table}.parquet")
    return f"{config.BASE_URL}/{table}.parquet"


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return None if v != v or v in (float("inf"), float("-inf")) else v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


class Database:
    def __init__(self) -> None:
        self._con: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.RLock()
        self._open_lock = threading.Lock()
        self._locked = False
        self._schema_cache: dict[str, list[dict]] = {}

    # ---- connection / setup -------------------------------------------------
    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is not None:
            return self._con
        con = duckdb.connect(":memory:")
        if config.LOCAL_DATA_DIR is not None:
            missing = [t for t in config.TABLES if not (config.LOCAL_DATA_DIR / f"{t}.parquet").is_file()]
            if missing:
                raise QueryError(
                    f"LOCAL_DATA_DIR={config.LOCAL_DATA_DIR} is missing {', '.join(m + '.parquet' for m in missing)}. "
                    "Run `python download_data.py` or unset LOCAL_DATA_DIR."
                )
        else:
            con.execute("INSTALL httpfs")
            con.execute("LOAD httpfs")
        con.execute(f"SET memory_limit='{config.MEMORY_LIMIT}'")
        con.execute(f"SET threads={int(config.THREADS)}")
        con.execute(f"SET http_timeout={int(config.HTTP_TIMEOUT_S)}")
        con.execute(f"SET http_retries={int(config.HTTP_RETRIES)}")
        con.execute("SET enable_http_metadata_cache=true")
        if config.HF_TOKEN and config.LOCAL_DATA_DIR is None:
            # Scoped to huggingface.co only, so the bearer token is never sent to the CDN
            # host that the redirect points at (that URL is already pre-signed).
            token = config.HF_TOKEN.replace("'", "")
            con.execute(
                "CREATE SECRET hf_http (TYPE HTTP, SCOPE 'https://huggingface.co/', "
                f"EXTRA_HTTP_HEADERS MAP {{'Authorization': 'Bearer {token}'}})"
            )
        self._con = con
        return con

    def _open_one(self, con: duckdb.DuckDBPyConnection, t: str) -> list[dict]:
        cur = con.cursor()
        try:
            cur.execute(f"CREATE OR REPLACE VIEW {t} AS SELECT * FROM read_parquet('{table_url(t)}')")
            # Resolve the schema now so remote problems surface here, with a clear message.
            rows = cur.execute(f"DESCRIBE {t}").fetchall()
        except Exception as e:  # noqa: BLE001
            try:
                cur.execute(f"DROP VIEW IF EXISTS {t}")
            except Exception:  # noqa: BLE001
                pass
            raise QueryError(self._explain(e, f"opening remote table '{t}'")) from e
        finally:
            cur.close()
        return [{"name": r[0], "type": r[1], "nullable": r[2] == "YES"} for r in rows]

    def _ensure_tables(self, tables: set[str] | None = None) -> None:
        """Open all seven remote tables once (in parallel), then lock the DuckDB config.

        After this, DuckDB itself refuses local file access, extension installs and config
        changes, and may only read the seven approved URLs - a second safety layer behind
        the SQLGlot validator.
        """
        if self._locked:
            return
        with self._open_lock:
            if self._locked:
                return
            con = self._connect()
            names = sorted(config.ALLOWED_TABLES)
            with ThreadPoolExecutor(max_workers=len(names)) as ex:
                results = list(ex.map(lambda t: (t, self._open_one(con, t)), names))
            for t, cols in results:
                self._schema_cache[t] = cols
            urls = ", ".join("'%s'" % table_url(t) for t in names)
            con.execute(f"SET allowed_paths=[{urls}]")
            con.execute("SET enable_external_access=false")
            con.execute("SET autoinstall_known_extensions=false")
            con.execute("SET autoload_known_extensions=false")
            con.execute("SET lock_configuration=true")
            self._locked = True

    def warm_up(self) -> None:
        """Open all tables in the background so the first tool call is fast."""
        try:
            self._ensure_tables()
            log.info("remote tables ready")
        except Exception as e:  # noqa: BLE001
            log.warning("warm-up failed (will retry on first use): %s", e)

    # ---- error translation --------------------------------------------------
    @staticmethod
    def _explain(e: Exception, ctx: str = "running query") -> str:
        msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        name = type(e).__name__
        low = str(e).lower()
        if isinstance(e, duckdb.InterruptException):
            return f"Query exceeded the {config.QUERY_TIMEOUT_S:g}s time limit and was cancelled. Add filters, select fewer columns, or aggregate earlier."
        if "out of memory" in low or isinstance(e, duckdb.OutOfMemoryException):
            return f"Query exceeded the {config.MEMORY_LIMIT} memory limit. Aggregate or filter before joining large tables (order_items, sessions)."
        if "401" in low or "403" in low or "unauthorized" in low or "forbidden" in low:
            return f"Hugging Face denied access while {ctx} (HTTP 401/403). Set HF_TOKEN to a read-only token if the dataset requires authentication."
        if "404" in low or "not found" in low and "http" in low:
            return f"Remote file not found while {ctx} (HTTP 404). Check HF_DATASET_REPO / HF_DATASET_REVISION."
        if "429" in low:
            return f"Hugging Face rate-limited the request while {ctx} (HTTP 429). Wait a moment and retry."
        if "permission error" in low or "disabled by configuration" in low:
            return "Access denied: only the seven approved remote tables can be read."
        if isinstance(e, (duckdb.HTTPException, duckdb.IOException)) or "http" in low or "timeout" in low:
            return f"Network/remote-file error while {ctx}: {msg[:300]}. This is usually transient; retry."
        if isinstance(e, (duckdb.BinderException, duckdb.CatalogException, duckdb.ParserException, duckdb.ConversionException)):
            return f"SQL error ({name.replace('Exception', '')}): {msg[:400]}"
        return f"Error while {ctx} ({name}): {msg[:400]}"

    # ---- public API ---------------------------------------------------------
    def table_columns(self, table: str) -> list[dict]:
        self._ensure_tables({table})
        return self._schema_cache[table]

    def sample_rows(self, table: str, n: int = 3) -> dict:
        self._ensure_tables({table})
        return self._run(f"SELECT * FROM {table} LIMIT {int(n)}", max_rows=n)

    def query(self, sql: str) -> dict:
        try:
            safe_sql = to_safe_sql(sql)
        except SQLValidationError as e:
            raise QueryError(f"Query rejected: {e}") from e
        refs = {
            t.name.lower()
            for t in sqlglot.parse_one(safe_sql, read="duckdb").find_all(exp.Table)
            if t.name.lower() in config.ALLOWED_TABLES
        }
        self._ensure_tables(refs)
        return self._run(safe_sql, max_rows=config.MAX_ROWS)

    def _run(self, safe_sql: str, max_rows: int) -> dict:
        with self._lock:
            cur = self._connect().cursor()
        timer = threading.Timer(config.QUERY_TIMEOUT_S, cur.interrupt)
        timer.start()
        start = time.perf_counter()
        try:
            cur.execute(safe_sql)
            columns = [d[0] for d in cur.description or []]
            fetched = cur.fetchmany(max_rows + 1)
        except Exception as e:  # noqa: BLE001
            raise QueryError(self._explain(e)) from e
        finally:
            timer.cancel()
            cur.close()
        truncated = len(fetched) > max_rows
        rows = [[_jsonable(v) for v in r] for r in fetched[:max_rows]]
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "execution_time_ms": round((time.perf_counter() - start) * 1000, 1),
        }


db = Database()
