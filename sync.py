"""
Sync engine for Keshet Teamim price files.
Uses requests.Session() for reliable cookie handling.
"""

import json
import logging
import os
import re
import time
import threading
from pathlib import Path
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

from fetcher import ProductDB

logger = logging.getLogger(__name__)

BASE_URL       = "https://url.publishedprices.co.il"
LOGIN_USER     = "Keshet"
LOGIN_PASS     = ""
DOWNLOAD_DIR   = Path(os.environ.get("PRICE_CACHE_DIR", "./price_cache"))
DELTA_INTERVAL = 1800

DOWNLOAD_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


class _Session:
    def __init__(self):
        self.csrf     = ""
        self._expires = 0.0
        self._s       = requests.Session()
        self._s.verify  = False
        self._s.headers.update(HEADERS)

    def _is_fresh(self):
        return self.csrf and time.time() < self._expires

    def ensure(self):
        if self._is_fresh():
            return
        self._login()
        self._fetch_csrf()
        self._expires = time.time() + 1800

    def _extract_csrf(self, html: str) -> str:
        m = re.search(r'name="csrftoken"\s+content="([^"]+)"', html) or \
            re.search(r'csrftoken[^"]*"\s*content="([^"]+)"', html) or \
            re.search(r'content="([^"]+)"\s*name="csrftoken"', html)
        return m.group(1) if m else ""

    def _login(self):
        logger.info("Logging in...")
        # GET login page first to establish session cookie + grab its CSRF
        r0 = self._s.get(f"{BASE_URL}/login", timeout=15)
        login_csrf = self._extract_csrf(r0.text)
        login_data = {"username": LOGIN_USER, "password": LOGIN_PASS}
        if login_csrf:
            login_data["csrftoken"] = login_csrf
        r = self._s.post(
            f"{BASE_URL}/login/user",
            data=login_data,
            allow_redirects=True,
            timeout=15,
        )
        logger.info(f"Login status: {r.status_code}")
        cookies = {c.name: c.value[:12] + "..." for c in self._s.cookies}
        logger.info(f"Cookies: {cookies}")

    def _fetch_csrf(self):
        """Fetch CSRF from a fresh GET /file — must be the last GET before the POST."""
        r = self._s.get(f"{BASE_URL}/file", timeout=15)
        logger.info(f"File page status: {r.status_code}")
        self.csrf = self._extract_csrf(r.text)
        if self.csrf:
            logger.info(f"Got csrftoken: {self.csrf[:12]}...")
        else:
            logger.warning("csrftoken not found in page")

    def post_form(self, url: str, fields: dict) -> bytes:
        self.ensure()
        r = self._s.post(
            url,
            data=fields,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/file",
            },
            timeout=20,
        )
        logger.info(f"POST {url} → {r.status_code} ({len(r.content)} bytes)")
        r.raise_for_status()
        return r.content

    def get(self, url: str) -> bytes:
        self.ensure()
        r = self._s.get(url, timeout=20)
        r.raise_for_status()
        return r.content


_session = _Session()


def list_remote_files() -> list[str]:
    try:
        _session.ensure()
        payload = {
            "sEcho": "1", "iColumns": "5", "sColumns": ",,,,",
            "iDisplayStart": "0", "iDisplayLength": "10000",
            "mDataProp_0": "fname",   "sSearch_0": "", "bRegex_0": "false", "bSearchable_0": "true", "bSortable_0": "true",
            "mDataProp_1": "typeLabel","sSearch_1": "", "bRegex_1": "false", "bSearchable_1": "true", "bSortable_1": "false",
            "mDataProp_2": "size",    "sSearch_2": "", "bRegex_2": "false", "bSearchable_2": "true", "bSortable_2": "true",
            "mDataProp_3": "ftime",   "sSearch_3": "", "bRegex_3": "false", "bSearchable_3": "true", "bSortable_3": "true",
            "mDataProp_4": "",        "sSearch_4": "", "bRegex_4": "false", "bSearchable_4": "true", "bSortable_4": "false",
            "sSearch": "", "bRegex": "false", "iSortingCols": "0",
            "cd": "/",
            "csrftoken": _session.csrf,
        }
        raw  = _session.post_form(f"{BASE_URL}/file/json/dir", payload)
        data = json.loads(raw)

        rows = data.get("aaData") or data.get("data") or []
        filenames = []
        for row in rows:
            fname = row[0] if isinstance(row, list) else row.get("fname", "")
            if fname.endswith(".gz") and not fname.startswith("NULL"):
                filenames.append(fname)

        logger.info(f"Found {len(filenames)} price files (excl. NULL*)")
        return filenames

    except Exception as e:
        logger.error(f"list_remote_files failed: {e}", exc_info=True)
        return []


def download_file(filename: str) -> Path | None:
    dest = DOWNLOAD_DIR / filename
    if dest.exists():
        return dest
    try:
        data = _session.get(f"{BASE_URL}/file/d/{filename}")
        dest.write_bytes(data)
        logger.info(f"Downloaded {filename} ({len(data):,} bytes)")
        return dest
    except Exception as e:
        logger.warning(f"Failed to download {filename}: {e}")
        return None


def sync_all(db: ProductDB) -> dict:
    filenames = list_remote_files()
    if not filenames:
        return {"error": "Could not list remote files"}

    downloaded = applied = skipped = 0
    for fname in filenames:
        local = download_file(fname)
        if not local:
            continue
        downloaded += 1
        n = db.sync_full(str(local)) if fname.startswith("PriceFull") \
            else db.sync_delta(str(local))
        if n > 0:
            applied += n
        else:
            skipped += 1

    result = {"files_found": len(filenames), "downloaded": downloaded,
              "rows_applied": applied, "skipped_unchanged": skipped}
    logger.info(f"Sync done: {result} | DB: {db.stats()}")
    return result


def load_local_file(db: ProductDB, filepath: str) -> int:
    fname = Path(filepath).name
    n = db.sync_full(filepath) if fname.startswith("PriceFull") \
        else db.sync_delta(filepath)
    logger.info(f"Local load {fname}: {n} rows")
    return n


def start_scheduler(db: ProductDB):
    def loop():
        logger.info("Initial price sync on startup...")
        sync_all(db)
        while True:
            time.sleep(DELTA_INTERVAL)
            logger.info("Scheduled price sync...")
            sync_all(db)
    t = threading.Thread(target=loop, daemon=True, name="price-sync")
    t.start()
    logger.info(f"Scheduler started (every {DELTA_INTERVAL}s)")
