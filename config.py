"""Configuration, loaded from environment variables (optionally a .env file)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:  # python-dotenv is optional at runtime
    pass

REPO = os.getenv("HF_DATASET_REPO", "HariJha/FreeEcommerceTwin14M")
# Pinned branch/revision. "main" by default; set to a commit sha for reproducibility.
REVISION = os.getenv("HF_DATASET_REVISION", "main")
BASE_URL = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}"

# Optional local mode: directory holding <table>.parquet (see download_data.py).
# When set, nothing is read over the network.
_local = os.getenv("LOCAL_DATA_DIR")
LOCAL_DATA_DIR = None
if _local:
    _p = Path(_local).expanduser()
    LOCAL_DATA_DIR = (_p if _p.is_absolute() else Path(__file__).parent / _p).resolve()

# Optional read-only token, only needed for private/gated datasets.
HF_TOKEN = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN") or None

MAX_ROWS = int(os.getenv("QUERY_MAX_ROWS", "100"))
QUERY_TIMEOUT_S = float(os.getenv("QUERY_TIMEOUT_SECONDS", "120"))
MEMORY_LIMIT = os.getenv("DUCKDB_MEMORY_LIMIT", "2GB")
THREADS = int(os.getenv("DUCKDB_THREADS", "4"))
HTTP_TIMEOUT_S = int(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))
# Max size of a SQL string accepted from the model.
MAX_SQL_CHARS = int(os.getenv("MAX_SQL_CHARS", "20000"))

TABLES: dict[str, dict] = {
    "customers": {
        "description": "One row per customer (1,000,000 rows): signup date, region, segment, lifetime stats.",
        "primary_key": ["customer_id"],
        "foreign_keys": [],
    },
    "products": {
        "description": "Product catalogue (50,000 rows): category, price, cost, brand, rating.",
        "primary_key": ["product_id"],
        "foreign_keys": [],
    },
    "sessions": {
        "description": "Web/app sessions (~7.5M rows): device, marketing channel, pages viewed, converted flag.",
        "primary_key": ["session_id"],
        "foreign_keys": [("customer_id", "customers", "customer_id")],
    },
    "orders": {
        "description": "Orders (~1.08M rows): status, totals, payment method.",
        "primary_key": ["order_id"],
        "foreign_keys": [
            ("customer_id", "customers", "customer_id"),
            ("session_id", "sessions", "session_id"),
        ],
    },
    "order_items": {
        "description": "Order line items (~4.3M rows): product, quantity, unit price, line total.",
        "primary_key": ["order_item_id"],
        "foreign_keys": [
            ("order_id", "orders", "order_id"),
            ("product_id", "products", "product_id"),
        ],
    },
    "returns": {
        "description": "Returned items (~400K rows): reason and refund amount.",
        "primary_key": ["return_id"],
        "foreign_keys": [
            ("order_id", "orders", "order_id"),
            ("order_item_id", "order_items", "order_item_id"),
            ("product_id", "products", "product_id"),
        ],
    },
    "support_tickets": {
        "description": "Customer support tickets (~67K rows): category, priority, status, resolution time.",
        "primary_key": ["ticket_id"],
        "foreign_keys": [
            ("customer_id", "customers", "customer_id"),
            ("order_id", "orders", "order_id"),
        ],
    },
}
ALLOWED_TABLES = frozenset(TABLES)
