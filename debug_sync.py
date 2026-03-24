"""Quick debug: check what list_remote_files actually gets back."""
import json
import logging
import os

os.environ["PRICE_DB_PATH"] = "./test_prices.db"
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")

from sync import _session, BASE_URL

_session.ensure()

# Exact same payload as list_remote_files but dump raw response
payload = {
    "sEcho": "1", "iColumns": "5", "sColumns": ",,,,",
    "iDisplayStart": "0", "iDisplayLength": "100",
    "mDataProp_0": "fname",   "sSearch_0": "", "bRegex_0": "false", "bSearchable_0": "true", "bSortable_0": "true",
    "mDataProp_1": "typeLabel","sSearch_1": "", "bRegex_1": "false", "bSearchable_1": "true", "bSortable_1": "false",
    "mDataProp_2": "size",    "sSearch_2": "", "bRegex_2": "false", "bSearchable_2": "true", "bSortable_2": "true",
    "mDataProp_3": "ftime",   "sSearch_3": "", "bRegex_3": "false", "bSearchable_3": "true", "bSortable_3": "true",
    "mDataProp_4": "",        "sSearch_4": "", "bRegex_4": "false", "bSearchable_4": "true", "bSortable_4": "false",
    "sSearch": "", "bRegex": "false", "iSortingCols": "0",
    "cd": "/",
    "csrftoken": _session.csrf,
}
raw = _session.post_form(f"{BASE_URL}/file/json/dir", payload)
print(f"\nRaw response ({len(raw)} bytes):")
print(raw.decode())
data = json.loads(raw)
print(f"\nKeys: {list(data.keys())}")
rows = data.get("aaData", data.get("data", []))
print(f"Rows: {len(rows)}")
if rows:
    print(f"First row: {rows[0]}")
    # Check structure
    r = rows[0]
    if isinstance(r, dict):
        print(f"Row keys: {list(r.keys())}")
