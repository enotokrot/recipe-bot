"""
Generate self-contained HTML catalog with tabs, search, and sort.
All product data embedded as JSON — works offline in any browser.
"""

import json
import html
from catalog import CATEGORY_KEYWORDS, browse_category, _pkg_size, _CATEGORY_FORMATS
from fetcher import ProductDB


def _esc(text: str) -> str:
    return html.escape(str(text))


def _product_to_dict(row, category: str = "") -> dict:
    name = row["item_name"] or ""
    mfr = (row["manufacturer"] or "").strip(" ,")
    price = row["price"] or 0
    is_weighted = bool(row["is_weighted"])
    size = _pkg_size(row)
    return {
        "name": name,
        "manufacturer": mfr if mfr and mfr not in ("לא ידוע", ",") else "",
        "price": round(price, 2),
        "size": size,
        "weighted": is_weighted,
        "code": row["item_code"] or "",
    }


def generate_catalog_html(db: ProductDB, category: str = "all") -> str:
    """Build a self-contained HTML catalog file."""

    # Collect products per category
    categories_data = {}
    if category == "all":
        cats_to_fetch = list(CATEGORY_KEYWORDS.keys())
    else:
        cats_to_fetch = [category] if category in CATEGORY_KEYWORDS else list(CATEGORY_KEYWORDS.keys())

    for cat in cats_to_fetch:
        products = browse_category(db, cat, limit=80)
        title, emoji = _CATEGORY_FORMATS.get(cat, (cat, "▪"))
        categories_data[cat] = {
            "title": title,
            "emoji": emoji,
            "products": [_product_to_dict(p, cat) for p in products],
        }

    data_json = json.dumps(categories_data, ensure_ascii=False)

    tab_buttons = []
    for cat in cats_to_fetch:
        info = categories_data[cat]
        count = len(info["products"])
        tab_buttons.append(
            f'<button class="tab" data-cat="{_esc(cat)}" onclick="showTab(\'{_esc(cat)}\')">'
            f'{info["emoji"]} {_esc(info["title"].split(" ", 1)[-1] if " " in info["title"] else info["title"])} '
            f'<span class="tab-count">{count}</span></button>'
        )

    tabs_html = "\n      ".join(tab_buttons)
    first_cat = cats_to_fetch[0] if cats_to_fetch else ""

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>קשת טעמים — קטלוג מוצרים</title>
<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #f5f5f0; --surface: #fff; --border: #e0dbd0;
    --text: #1a1714; --text2: #6b6560; --text3: #9e9891;
    --accent: #2d5016; --accent-light: #e8f0de;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Heebo', sans-serif;
    background: var(--bg); color: var(--text);
    padding: 16px; max-width: 960px; margin: 0 auto;
  }}
  .header {{
    display: flex; justify-content: space-between; align-items: center;
    padding-bottom: 16px; margin-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }}
  .header h1 {{ font-size: 22px; font-weight: 600; }}
  .header .sub {{ font-size: 12px; color: var(--text3); }}
  .search-box {{
    width: 100%; padding: 10px 14px; border: 1px solid var(--border);
    border-radius: 10px; font-size: 14px; font-family: inherit;
    margin-bottom: 12px; background: var(--surface);
  }}
  .search-box:focus {{ outline: none; border-color: var(--accent); }}
  .tabs {{
    display: flex; gap: 6px; overflow-x: auto; padding-bottom: 8px;
    margin-bottom: 16px; -webkit-overflow-scrolling: touch;
  }}
  .tab {{
    padding: 6px 14px; border: 1px solid var(--border); border-radius: 20px;
    background: var(--surface); font-size: 13px; font-family: inherit;
    cursor: pointer; white-space: nowrap; transition: all 0.15s;
  }}
  .tab:hover {{ background: var(--accent-light); }}
  .tab.active {{
    background: var(--accent); color: white; border-color: var(--accent);
  }}
  .tab-count {{
    font-size: 10px; opacity: 0.7; margin-right: 2px;
  }}
  .sort-bar {{
    display: flex; gap: 8px; margin-bottom: 12px; font-size: 12px; color: var(--text3);
  }}
  .sort-btn {{
    background: none; border: none; font-family: inherit; font-size: 12px;
    color: var(--text3); cursor: pointer; padding: 2px 6px; border-radius: 4px;
  }}
  .sort-btn:hover {{ background: var(--accent-light); }}
  .sort-btn.active {{ color: var(--accent); font-weight: 500; }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 10px;
  }}
  .card {{
    background: var(--surface); border: 0.5px solid var(--border);
    border-radius: 10px; padding: 14px; transition: box-shadow 0.15s;
  }}
  .card:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .card-name {{ font-weight: 500; font-size: 14px; line-height: 1.3; }}
  .card-mfr {{ font-size: 11px; color: var(--text3); margin-top: 2px; }}
  .card-price {{
    font-size: 18px; font-weight: 600; color: var(--accent); margin-top: 8px;
  }}
  .card-size {{ font-size: 11px; color: var(--text3); }}
  .empty {{ text-align: center; padding: 40px; color: var(--text3); }}
  .footer {{
    text-align: center; font-size: 11px; color: var(--text3);
    margin-top: 24px; padding-top: 16px; border-top: 0.5px solid var(--border);
  }}
  @media (max-width: 600px) {{
    .grid {{ grid-template-columns: 1fr; }}
    body {{ padding: 10px; }}
  }}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>🛒 קשת טעמים</h1>
    <div class="sub">קטלוג מוצרים · מחירים עדכניים</div>
  </div>
  <div style="font-size:11px;color:var(--text3)" id="date-display"></div>
</div>

<input class="search-box" type="text" placeholder="🔍 חיפוש מוצר..." id="search" oninput="filterProducts()">

<div class="tabs" id="tabs">
  {tabs_html}
</div>

<div class="sort-bar">
  מיון:
  <button class="sort-btn active" onclick="sortBy('price')">מחיר ↑</button>
  <button class="sort-btn" onclick="sortBy('name')">שם א-ת</button>
</div>

<div class="grid" id="grid"></div>

<div class="footer">
  נוצר על ידי Recipe Price Bot · קשת טעמים
</div>

<script>
const DATA = {data_json};
let currentCat = '{_esc(first_cat)}';
let currentSort = 'price';
let searchQuery = '';

function showTab(cat) {{
  currentCat = cat;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.tab[data-cat="${{cat}}"]`).classList.add('active');
  render();
}}

function sortBy(field) {{
  currentSort = field;
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  render();
}}

function filterProducts() {{
  searchQuery = document.getElementById('search').value.trim().toLowerCase();
  render();
}}

function render() {{
  const cat = DATA[currentCat];
  if (!cat) {{ document.getElementById('grid').innerHTML = '<div class="empty">לא נמצאו מוצרים</div>'; return; }}
  let products = [...cat.products];
  if (searchQuery) {{
    products = products.filter(p =>
      p.name.toLowerCase().includes(searchQuery) ||
      p.manufacturer.toLowerCase().includes(searchQuery)
    );
  }}
  if (currentSort === 'price') products.sort((a,b) => a.price - b.price);
  else products.sort((a,b) => a.name.localeCompare(b.name, 'he'));

  if (!products.length) {{
    document.getElementById('grid').innerHTML = '<div class="empty">לא נמצאו מוצרים</div>';
    return;
  }}
  document.getElementById('grid').innerHTML = products.map(p => `
    <div class="card">
      <div class="card-name">${{p.name}}</div>
      ${{p.manufacturer ? `<div class="card-mfr">${{p.manufacturer}}</div>` : ''}}
      <div class="card-price">₪${{p.price.toFixed(2)}}</div>
      <div class="card-size">${{p.size || ''}}</div>
    </div>
  `).join('');
}}

// Init
document.getElementById('date-display').textContent =
  new Intl.DateTimeFormat('he-IL', {{day:'numeric',month:'long',year:'numeric'}}).format(new Date());
if (document.querySelector('.tab')) document.querySelector('.tab').classList.add('active');
render();
</script>
</body>
</html>"""
