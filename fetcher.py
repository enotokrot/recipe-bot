"""
Persistent product database using SQLite.
- PriceFull files rebuild the DB (run once daily)
- Price delta files apply only changed rows (run every 30min)
- rapidfuzz index is rebuilt lazily after any update
"""

import gzip
import hashlib
import logging
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("PRICE_DB_PATH", "./prices.db")

DDL = """
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id        TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    item_code       TEXT NOT NULL,
    item_name       TEXT NOT NULL,
    manufacturer    TEXT,
    price           REAL NOT NULL,
    unit_price      REAL,
    unit_of_measure TEXT,
    quantity        REAL,
    is_weighted     INTEGER DEFAULT 0,
    updated_at      TEXT,
    UNIQUE(chain_id, store_id, item_code)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT UNIQUE,
    file_hash     TEXT,
    synced_at     REAL,
    rows_affected INTEGER
);

CREATE INDEX IF NOT EXISTS idx_item_name ON products(item_name);
CREATE INDEX IF NOT EXISTS idx_chain_store ON products(chain_id, store_id);
"""


@dataclass
class Product:
    id: int
    code: str
    name: str
    manufacturer: str
    price: float
    unit_price: float
    unit_of_measure: str
    quantity: float
    is_weighted: bool
    store_id: str
    chain_id: str

    def price_display(self) -> str:
        if self.is_weighted:
            return f"₪{self.price:.2f}/{self.unit_of_measure}"
        return f"₪{self.price:.2f}"


def _get_conn(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn


def _parse_xml_bytes(raw: bytes, store_id: str, chain_id: str) -> list[dict]:
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    raw = raw.lstrip(b'\xef\xbb\xbf')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        logger.error(f"XML parse error: {e}")
        return []
    chain_id = root.findtext("ChainId") or chain_id
    store_id = root.findtext("StoreId") or store_id
    rows = []
    for item in root.findall(".//Item"):
        try:
            rows.append({
                "chain_id":        chain_id,
                "store_id":        store_id,
                "item_code":       item.findtext("ItemCode") or "",
                "item_name":       item.findtext("ItemName") or "",
                "manufacturer":    (item.findtext("ManufacturerName") or "").strip(" ,"),
                "price":           float(item.findtext("ItemPrice") or 0),
                "unit_price":      float(item.findtext("UnitOfMeasurePrice") or 0),
                "unit_of_measure": item.findtext("UnitOfMeasure") or "",
                "quantity":        float(item.findtext("Quantity") or 1),
                "is_weighted":     1 if item.findtext("bIsWeighted") == "1" else 0,
                "updated_at":      item.findtext("PriceUpdateDate") or "",
            })
        except (ValueError, TypeError):
            continue
    return rows


def _filename_meta(fname: str) -> tuple[str, str]:
    m = re.search(r"(\d{13})-(\d+)-", fname)
    return (m.group(1), m.group(2)) if m else ("unknown", "unknown")


def _file_hash(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _already_synced(conn: sqlite3.Connection, filename: str, fhash: str) -> bool:
    row = conn.execute(
        "SELECT file_hash FROM sync_log WHERE filename=?", (filename,)
    ).fetchone()
    return row is not None and row["file_hash"] == fhash


def _record_sync(conn, filename, fhash, rows_affected):
    conn.execute("""
        INSERT INTO sync_log(filename, file_hash, synced_at, rows_affected)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(filename) DO UPDATE SET
            file_hash=excluded.file_hash,
            synced_at=excluded.synced_at,
            rows_affected=excluded.rows_affected
    """, (filename, fhash, time.time(), rows_affected))
    conn.commit()


def sync_full(filepath: str, conn=None) -> int:
    """Load a PriceFull file. UPSERT — safe to call repeatedly; skips if file unchanged."""
    close_after = conn is None
    conn = conn or _get_conn()
    fname = Path(filepath).name
    raw = Path(filepath).read_bytes()
    fhash = _file_hash(raw)
    if _already_synced(conn, fname, fhash):
        logger.info(f"[sync_full] {fname} unchanged, skipping")
        return 0
    chain_id, store_id = _filename_meta(fname)
    rows = _parse_xml_bytes(raw, store_id, chain_id)
    conn.executemany("""
        INSERT INTO products
            (chain_id, store_id, item_code, item_name, manufacturer,
             price, unit_price, unit_of_measure, quantity, is_weighted, updated_at)
        VALUES
            (:chain_id, :store_id, :item_code, :item_name, :manufacturer,
             :price, :unit_price, :unit_of_measure, :quantity, :is_weighted, :updated_at)
        ON CONFLICT(chain_id, store_id, item_code) DO UPDATE SET
            item_name       = excluded.item_name,
            manufacturer    = excluded.manufacturer,
            price           = excluded.price,
            unit_price      = excluded.unit_price,
            unit_of_measure = excluded.unit_of_measure,
            quantity        = excluded.quantity,
            is_weighted     = excluded.is_weighted,
            updated_at      = excluded.updated_at
        WHERE excluded.updated_at > products.updated_at
    """, rows)
    conn.commit()
    n = len(rows)
    _record_sync(conn, fname, fhash, n)
    logger.info(f"[sync_full] {fname}: upserted {n} products")
    if close_after:
        conn.close()
    return n


def sync_delta(filepath: str, conn=None) -> int:
    """Apply a delta file — only updates rows that actually changed. Skip if file unchanged."""
    close_after = conn is None
    conn = conn or _get_conn()
    fname = Path(filepath).name
    raw = Path(filepath).read_bytes()
    fhash = _file_hash(raw)
    if _already_synced(conn, fname, fhash):
        logger.info(f"[sync_delta] {fname} already applied, skipping")
        return 0
    chain_id, store_id = _filename_meta(fname)
    rows = _parse_xml_bytes(raw, store_id, chain_id)
    if not rows:
        return 0
    cursor = conn.executemany("""
        UPDATE products SET
            price           = :price,
            unit_price      = :unit_price,
            unit_of_measure = :unit_of_measure,
            updated_at      = :updated_at
        WHERE chain_id  = :chain_id
          AND store_id  = :store_id
          AND item_code = :item_code
          AND :updated_at > updated_at
    """, rows)
    conn.commit()
    n = cursor.rowcount
    _record_sync(conn, fname, fhash, n)
    logger.info(f"[sync_delta] {fname}: updated {n} prices")
    if close_after:
        conn.close()
    return n


class ProductDB:
    """Query wrapper. Keeps name list in RAM for rapidfuzz; refreshes lazily after updates."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = _get_conn(db_path)
        self._name_cache: list[tuple[int, str]] = []
        self._cache_dirty = True

    def sync_full(self, filepath: str) -> int:
        n = sync_full(filepath, self.conn)
        if n:
            self._cache_dirty = True
        return n

    def sync_delta(self, filepath: str) -> int:
        n = sync_delta(filepath, self.conn)
        if n:
            self._cache_dirty = True
        return n

    def _refresh_cache(self):
        if not self._cache_dirty:
            return
        rows = self.conn.execute("SELECT id, item_name FROM products ORDER BY id").fetchall()
        self._name_cache = [(r["id"], r["item_name"]) for r in rows]
        self._cache_dirty = False
        logger.info(f"Name cache rebuilt: {len(self._name_cache)} products")

    def all_names(self) -> list[tuple[int, str]]:
        self._refresh_cache()
        return self._name_cache

    def get(self, row_id: int) -> Optional[Product]:
        row = self.conn.execute("SELECT * FROM products WHERE id=?", (row_id,)).fetchone()
        if not row:
            return None
        return Product(
            id=row["id"], code=row["item_code"], name=row["item_name"],
            manufacturer=row["manufacturer"] or "", price=row["price"],
            unit_price=row["unit_price"] or 0, unit_of_measure=row["unit_of_measure"] or "",
            quantity=row["quantity"] or 1, is_weighted=bool(row["is_weighted"]),
            store_id=row["store_id"], chain_id=row["chain_id"],
        )

    def is_empty(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0

    def stats(self) -> dict:
        count = self.conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        last = self.conn.execute(
            "SELECT filename, synced_at FROM sync_log ORDER BY synced_at DESC LIMIT 1"
        ).fetchone()
        return {
            "total_products": count,
            "last_sync_file": last["filename"] if last else None,
            "last_sync_age_min": round((time.time() - last["synced_at"]) / 60) if last else None,
        }
