"""One-time download of the seven Parquet files for local mode (resumable, parallel).

Usage: python download_data.py [target_dir]     (default: ./data_cache)
Then set LOCAL_DATA_DIR=<target_dir> (e.g. in .env) to make the server use the local copy.
"""
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config

DEST = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "data_cache")


def fetch(table: str) -> str:
    url = f"{config.BASE_URL}/{table}.parquet"
    final, part = DEST / f"{table}.parquet", DEST / f"{table}.parquet.part"
    hdr = {"Authorization": f"Bearer {config.HF_TOKEN}"} if config.HF_TOKEN else {}
    head = urllib.request.Request(url, headers=hdr, method="HEAD")
    with urllib.request.urlopen(head, timeout=60) as r:
        total = int(r.headers["Content-Length"])
    if final.exists() and final.stat().st_size == total:
        return f"{table}: already complete"
    for attempt in range(20):  # resume on stalls/disconnects
        have = part.stat().st_size if part.exists() else 0
        if have == total:
            break
        req = urllib.request.Request(url, headers={**hdr, "Range": f"bytes={have}-"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        except Exception as e:  # noqa: BLE001
            print(f"{table}: retry {attempt + 1} ({e})", flush=True)
    if part.stat().st_size != total:
        raise RuntimeError(f"{table}: incomplete download")
    part.rename(final)
    return f"{table}: {total / 1e6:.1f} MB ok"


if __name__ == "__main__":
    DEST.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(7) as ex:
        for msg in ex.map(fetch, config.TABLES):
            print(msg, flush=True)
