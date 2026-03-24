"""
Product catalog browsing and search for Telegram commands.
"""

import re as _re
from fetcher import ProductDB

# Each category has: include (search keywords), exclude (reject if found),
# require_in_name (product name MUST contain one of these), min_price floor.
CATEGORY_KEYWORDS = {
    "whiskey": {
        "include": ["וויסקי", "ויסקי", "whisky", "whiskey", "בורבון", "scotch"],
        "exclude": ["כוס", "כוסות", "צעצוע", "סוויט בוקס", "מדבקה", "פותחן",
                     "בונ ", "שוקולד", "קפה נמס", "גבינה", "רוטב", "קרוטון"],
        "min_price": 20,
    },
    "wine": {
        "include": ["יין", "רוזה", "קברנה", "שרדונה", "מרלו"],
        "exclude": ["כוס", "כוסות", "פותחן", "מקרר", "מדף", "חומץ", "רוטב"],
        "min_price": 15,
    },
    "beer": {
        "include": ["בירה", "לאגר", "אייל", "beer"],
        "exclude": ["כוס", "כוסות", "פותחן", "שמרי", "רוטב", "בטעם בירה"],
        "min_price": 3,
    },
    "cheese": {
        "include": ["גבינת", "גבינה ", "מוצרלה", "פרמזן", "צ'דר", "בולגרית",
                     "גאודה", "קממבר", "ריקוטה", "קוטג", "חלומי", "עמק"],
        "exclude": ["חטיף", "קרקר", "שוש", "ביסלי", "במבה", "בטעם גבינה", "אצבעות",
                     "מרק", "מנטוס", "סוכריה", "מסטיק", "פיצה", "בורקס", "קרוטון",
                     "תירס", "פופקורן", "צ'יפס"],
    },
    "sausage": {
        "include": ["נקניק", "נקניקייה", "נקניקיות", "סלמי", "פסטרמה",
                     "קבנוסי", "מרגז", "צ'וריסו", "שינקן", "בייקון",
                     "לנצ'ון", "הוט דוג", "פרנקפורטר", "ויינר"],
        "exclude": ["מזון לחתול", "מזון לכלב", "מזון לחיות", "pet food",
                     "בטעם בייקון", "בטעם נקניק", "טעם בייקון",
                     "קרוטונים", "ביסלי", "במבה", "חטיף", "צ'יפס", "סוחריקי",
                     "ממרח", "פטה",
                     "כבד עוף טרי", "כבד בקר", "כבד אווז טרי",
                     "נקניקיות", "נקניקייה", "ילדים", "וינר", "וינה",
                     "הוט דוג", "בוואריה", "פרגית", "פס שחור", "פס אדום",
                     "ניילון", "קנקרס", "בוואק"],
        "require_in_name": ["סלמי", "פסטרמה", "קבנוסי",
                             "מרגז", "שינקן", "בייקון", "לנצ'ון",
                             "צ'וריסו", "כורכמר", "קרקוב",
                             "לוביטלסקי", "יזיקוביה",
                             "מורטדלה", "ספק", "פרושוטו", "קולומביה"],
    },
    "seafood": {
        "include": ["דג", "סלמון", "פורל", "דניס", "בס ים", "קרפיון",
                     "מוסר", "לוקוס", "ברמונדי", "טילפיה", "אמנון",
                     "פאנגסיוס", "הליבוט", "קוד", "בקלה"],
        "exclude": ["טונה", "שרימפס", "קלמרי",
                     "קפוא", "קפואה", "frozen",
                     "מעושן", "מלוח", "משומר", "מיובש", "כבוש",
                     "קופסה", "שימורי", "בשמן", "ברוטב", "מוחמץ", "מרינד",
                     "מבושל", "מטוגן", "קציצ", "כדור", "בורגר",
                     "פיש סטיק", "גפילטע", "גפילטה",
                     "ממרח", "פטה", "פשטידה",
                     "מלח דג", "דג מלח", "אנשובי", "סרדינ",
                     "הרינג", "מטיאס",
                     "דג זהב", "מזון לדג", "מזון לחתול", "מזון לכלב",
                     "רוטב", "בטעם", "צ'יפס", "קרקר"],
        "weighted_only": True,
    },
    "meat": {
        "include": ["בקר", "עגל", "כבש", "טלה",
                     "אנטריקוט", "שייטל", "כתף",
                     "צלעות", "אסאדו", "סינטה", "פיקניה",
                     "בריסקט", "חזה בקר", "שפונדרה",
                     "צוואר", "זנב", "לשון",
                     "קציצות בקר", "המבורגר בקר",
                     "טחון בקר", "טחון עגל", "טחון כבש"],
        "exclude": ["עוף", "הודו", "ברווז",
                     "נקניק", "פסטרמה", "סלמי",
                     "קפוא", "frozen", "מרינד",
                     "מוכן", "מבושל", "מעושן",
                     "שווארמה מוכנה",
                     "כבד", "לב", "ריאה", "טחול",
                     "דג", "סלמון", "אמנון", "פורל", "דניס", "בקלה",
                     "תיבול", "מתובל", "בטריאק", "בפפריקה", "בשום",
                     "מוכן לאכילה", "מוכן לחימום",
                     "קורנד", "קרנד", "שימורי"],
        "weighted_only": True,
    },
    "poultry": {
        "include": ["עוף", "פרגית", "שוק עוף", "חזה עוף",
                     "כנפיים", "ירך עוף", "שלם", "פולארד",
                     "הודו", "ברווז", "אווז",
                     "קציצות עוף", "המבורגר עוף",
                     "טחון עוף", "טחון הודו"],
        "exclude": ["נקניקיות עוף", "קפוא",
                     "מוכן", "מבושל", "כבד עוף"],
        "weighted_only": True,
    },
    "vodka": {
        "include": ["וודקה", "vodka"],
        "exclude": ["כוס", "כוסות", "רוטב", "פסטה"],
        "min_price": 20,
    },
    "spirits": {
        "include": ["קוניאק", "ברנדי", "ג'ין", "טקילה", "ליקר",
                     "אמרטו", "מרטיני", "רום "],
        "exclude": ["כוס", "כוסות", "פותחן", "רוטב", "גלידה", "בטעם", "עוגה"],
        "min_price": 20,
    },
    "chocolate": {
        "include": ["שוקולד", "פרלין", "טראפל"],
        "exclude": ["גלידה", "משקה", "אבקת", "ממרח", "סירופ"],
    },
    "coffee": {
        "include": ["קפה", "אספרסו", "coffee"],
        "exclude": ["גלידה", "בטעם קפה", "סוכריה", "עוגת"],
    },
}


def _pkg_size(p) -> str:
    """Return clean package size string."""
    qty = p["quantity"] or 0
    unit = (p["unit_of_measure"] or "").strip()

    if p["is_weighted"]:
        return 'ק"ג'
    if not qty or qty <= 0:
        return ""

    if "100" in unit:
        base_unit = (
            unit.replace("100", "").replace("גרם", "גר").replace("מיליליטר", 'מ"ל').strip()
        )
        return f"{qty:.0f} {base_unit}"

    unit = unit.replace("מיליליטר", 'מ"ל').replace("גרם", "גר").replace("ליטר", "ל׳")

    if qty >= 1000 and "גר" in unit:
        kg = qty / 1000
        return f"{kg:g} ק\"ג"
    if qty >= 1000 and 'מ"ל' in unit:
        l = qty / 1000
        return f"{l:g} ל׳"

    if qty == int(qty):
        return f"{int(qty)} {unit}"
    return f"{qty:.0f} {unit}"


def _truncate(name: str, limit: int = 28) -> str:
    """Truncate at last space before limit for clean display."""
    if len(name) <= limit:
        return name
    truncated = name[:limit]
    last_space = truncated.rfind(" ")
    if last_space > limit - 8:
        return truncated[:last_space] + "…"
    return truncated + "…"


def _dedup_by_name(products: list) -> list:
    """Keep one entry per normalized name — cheapest price."""
    seen: dict[str, dict] = {}
    for p in products:
        name = (p["item_name"] or "").strip()
        key = _re.sub(r"\d+", "", name).strip().lower()
        key = _re.sub(r"\s+", " ", key)
        if key not in seen:
            seen[key] = p
        elif p["price"] < seen[key]["price"]:
            seen[key] = p
    return list(seen.values())


def search_products(db: ProductDB, query: str, limit: int = 30) -> list:
    """Search products by keyword. Returns sorted by price."""
    query_escaped = query.replace("'", "''")
    rows = db.conn.execute(
        "SELECT * FROM products WHERE (item_name LIKE ? OR manufacturer LIKE ?) "
        "AND price > 0 ORDER BY price ASC LIMIT ?",
        (f"%{query_escaped}%", f"%{query_escaped}%", limit),
    ).fetchall()
    return rows


def browse_category(db: ProductDB, category: str, limit: int = 25) -> list:
    """Browse products by category config: include/exclude/require/min_price."""
    cat_lower = category.lower()
    config = CATEGORY_KEYWORDS.get(cat_lower)

    # Simple string fallback for unknown categories
    if config is None:
        return search_products(db, category, limit)

    include_kws = config.get("include", [])
    exclude_kws = config.get("exclude", [])
    require_kws = config.get("require_in_name", [])
    min_price = config.get("min_price", 0)

    results = []
    seen = set()

    for keyword in include_kws:
        kw_escaped = keyword.replace("'", "''")
        for row in db.conn.execute(
            "SELECT * FROM products WHERE item_name LIKE ?",
            (f"%{kw_escaped}%",),
        ).fetchall():
            code = row["item_code"]
            if code in seen:
                continue
            name = (row["item_name"] or "").lower()
            price = row["price"] or 0

            # Price floor
            if price <= min_price:
                continue

            # Require check: name must contain at least one require keyword
            if require_kws and not any(rk in name for rk in require_kws):
                continue

            # Exclude check
            if any(ex in name for ex in exclude_kws):
                continue

            seen.add(code)
            results.append(row)

    # Weighted-only categories: fresh counter products sold by kg
    if config.get("weighted_only"):
        results = [r for r in results if r["is_weighted"] == 1]

    results = _dedup_by_name(results)
    results.sort(key=lambda x: x["price"])
    return results[:limit]


_CATEGORY_FORMATS = {
    "whiskey":   ("🥃 וויסקי & מולט", "🥃"),
    "wine":      ("🍷 יינות", "🍷"),
    "beer":      ("🍺 בירות", "🍺"),
    "cheese":    ("🧀 גבינות", "🧀"),
    "sausage":   ("🌭 נקניקים ומעדנים", "🌭"),
    "seafood":   ("🐟 דגים טריים", "🐟"),
    "meat":      ("🥩 בשר טרי", "🥩"),
    "poultry":   ("🐔 עוף ועופות", "🐔"),
    "vodka":     ("🍸 וודקה", "🍸"),
    "spirits":   ("🥂 משקאות חריפים", "🥃"),
    "chocolate": ("🍫 שוקולד ופרלינים", "🍫"),
    "coffee":    ("☕ קפה", "☕"),
}

_DYNAMIC_EMOJI = {
    "wine": lambda p: "🍷" if "לבן" in (p["item_name"] or "") else (
        "🍾" if "אדום" in (p["item_name"] or "") else "🌸"
    ),
    "sausage": lambda p: "🥩" if any(
        w in (p["item_name"] or "") for w in ["פסטרמה", "בקר", "מרגז"]
    ) else "🌭",
    "seafood": lambda p: "🍣" if "סלמון" in (p["item_name"] or "") else "🐟",
    "spirits": lambda p: "🥃" if any(
        w in (p["item_name"] or "") for w in ["ויסקי", "ברנדי", "קוניאק"]
    ) else "🍸",
}


def format_product_list(products: list, title: str, category: str = "") -> str:
    """Format product list for Telegram HTML with category-aware formatting."""
    if not products:
        return f"❌ לא נמצאו מוצרים עבור: {title}"

    cat_lower = category.lower() if category else ""
    header_title, default_emoji = _CATEGORY_FORMATS.get(cat_lower, (title, "▪"))
    emoji_fn = _DYNAMIC_EMOJI.get(cat_lower)

    display_title = header_title if cat_lower in _CATEGORY_FORMATS else title

    # Price range summary for weighted categories
    prices = [p["price"] for p in products if p["price"] > 0]
    all_weighted = all(p["is_weighted"] for p in products)
    if prices and all_weighted and len(prices) > 1:
        subtitle = f'{len(products)} מוצרים · ₪{min(prices):.0f} — ₪{max(prices):.0f} לק"ג'
    else:
        subtitle = f"{len(products)} מוצרים · ממוין לפי מחיר"

    lines = [
        f"<b>{display_title}</b>",
        f"<i>{subtitle}</i>",
        "——————————————————",
    ]

    for p in products:
        emoji = emoji_fn(p) if emoji_fn else default_emoji
        name = _truncate(p["item_name"] or "")
        mfr = (p["manufacturer"] or "").strip(" ,")
        mfr_str = f" · <i>{_truncate(mfr, 18)}</i>" if mfr and mfr not in ("לא ידוע", ",", "") else ""
        price = p["price"]
        size = _pkg_size(p)
        suffix = f" / {size}" if size else ""

        lines.append(f"{emoji} <b>{name}</b>{mfr_str}")
        lines.append(f"   ₪{price:.2f}{suffix}")
        lines.append("")

    lines.append("——————————————————")
    lines.append("<i>מחירים מקשת טעמים · מתעדכן כל 30 דק׳</i>")
    return "\n".join(lines)
