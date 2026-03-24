"""
RecipePricer: Claude-powered ingredient extraction (text + image) + product matching.
Output is HTML formatted for Telegram (parse_mode="HTML").
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
import httpx
from rapidfuzz import fuzz, process
from fetcher import ProductDB, Product
from ingredients_en_he import translate_to_hebrew

logger = logging.getLogger(__name__)

CLAUDE_API   = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"
FUZZY_TOP_N  = 15
SHOW_ALTS    = 2   # alternatives per ingredient


@dataclass
class Ingredient:
    name: str
    quantity: float
    unit: str
    optional: bool = False
    alternatives: list[str] = field(default_factory=list)  # recipe hints like "חזה בקר", "כתף בקר"


@dataclass
class CostInfo:
    recipe_cost: float      # cost for recipe quantity
    full_price: float       # full package/unit price
    pkg_desc: str = ""      # e.g. "20 גר", "1 ל"
    is_partial: bool = False
    buy_full: bool = False  # must buy whole package (small condiment)
    note: str = ""          # warning like "⚠ משקל לא ידוע"


@dataclass
class MatchedIngredient:
    ingredient: Ingredient
    matches: list[Product]
    not_found: bool = False
    suspicious: bool = False
    zero_price: bool = False
    cost_info: Optional[CostInfo] = None
    alt_costs: list[CostInfo] = field(default_factory=list)

    def best(self) -> Optional[Product]:
        return self.matches[0] if self.matches else None


def _check_match_quality(ingredient_name: str, product_name: str) -> bool:
    """Return True if suspicious (fewer than 2 shared Hebrew chars)."""
    ing_chars = set(c for c in ingredient_name if '\u0590' <= c <= '\u05FF')
    prod_chars = set(c for c in product_name if '\u0590' <= c <= '\u05FF')
    return len(ing_chars & prod_chars) < 2


# ── Emoji category mapping ────────────────────────────────────────────────────

_EMOJI_RULES = [
    (["בקר", "עוף", "כבש", "טלה", "שייטל", "כתף", "חזה", "צלע", "סטייק",
      "אנטריקוט", "פילה", "שניצל", "כרעיים", "שוק", "צוואר"], "🥩"),
    (["בצל", "עגבני", "גזר", "מלפפון", "קישוא", "חציל", "בטטה",
      "תפוח אדמה", "פלפל ירוק", "פלפל אדום", "סלרי", "כרוב"], "🧅"),
    (["שמן"], "🫒"),
    (["שום"], "🧄"),
    (["פטרי", "שמפיניון", "שימגי", "פורטובלו"], "🍄"),
    (["חרדל", "קטשופ", "מיונז", "רוטב", "חומץ", "טחינה"], "🫙"),
    (["סילאן", "דבש", "סוכר", "ממרח", "ריבה"], "🍯"),
    (["יין", "בירה", "משקה"], "🍷"),
    (["מלח"], "🧂"),
    (["פלפל שחור", "כמון", "פפריקה", "כורכום", "קינמון", "תבלין"], "🌶"),
    (["ביצה", "ביצים"], "🥚"),
    (["קמח", "לחם", "פיתה", "חלה"], "🍞"),
    (["חלב", "שמנת", "גבינה", "יוגורט", "חמאה"], "🧈"),
    (["לימון", "תפוז", "תפוח", "בננה", "אבוקדו"], "🍋"),
]


def _emoji_for(ingredient_name: str) -> str:
    name_lower = ingredient_name
    for keywords, emoji in _EMOJI_RULES:
        for kw in keywords:
            if kw in name_lower:
                return emoji
    return "▪"


# ── Cost estimation ────────────────────────────────────────────────────────────

TYPICAL_WEIGHTS = {
    # Vegetables
    "בצל": 150, "בצלים": 150,
    "פלפל": 150, "פלפלים": 150,
    "פלפל צבעוני": 150,
    "פלפל אדום": 150, "פלפל ירוק": 150, "פלפל צהוב": 150,
    "עגבנייה": 120, "עגבניות": 120,
    "עגבנייה שרי": 15, "עגבניות שרי": 15,
    "מלפפון": 200, "מלפפונים": 200,
    "גזר": 80, "גזרים": 80,
    "תפוח אדמה": 150, "תפוחי אדמה": 150,
    "בטטה": 200, "בטטות": 200,
    "חציל": 300, "חצילים": 300,
    "קישוא": 200, "קישואים": 200,
    "שן שום": 5, "שיני שום": 5, "שום": 5,
    "ביצה": 60, "ביצים": 60,
    "לימון": 100, "לימונים": 100,
    "תפוז": 180, "תפוזים": 180,
    "אבוקדו": 180, "אבוקדואים": 180,
    "תפוח": 180, "תפוחים": 180,
    "בננה": 120, "בננות": 120,
    # Mushrooms
    "פטריה": 30, "פטריות": 100,
    "פטריות שמפיניון": 100,
    "פטריות פורטובלו": 80,
}
_DEFAULT_UNIT_WEIGHT_G = 150  # fallback for unknown unit items (typical vegetable)

_TO_GRAMS = {
    # Metric
    "g": 1, "גרם": 1, "gr": 1, "gram": 1, "grams": 1,
    "kg": 1000, 'ק"ג': 1000, "קג": 1000, "kilogram": 1000,
    "ml": 1, "מל": 1, "milliliter": 1,
    "l": 1000, "ליטר": 1000, "liter": 1000, "litre": 1000,
    # Imperial
    "oz": 28.35, "ounce": 28.35, "ounces": 28.35,
    "lb": 453.59, "lbs": 453.59, "pound": 453.59, "pounds": 453.59,
    "fl oz": 29.57, "fluid oz": 29.57,
    # Volume measures
    "cup": 240, "cups": 240, "כוס": 240, "כוסות": 240,
    "tbsp": 15, "tablespoon": 15, "tablespoons": 15, "כפות": 15, "כף": 15,
    "tsp": 5, "teaspoon": 5, "teaspoons": 5, "כפית": 5, "כפיות": 5,
    "pinch": 0.5, "קורט": 0.5,
}


def _pkg_desc(product: Product) -> str:
    """Human-readable package description from product metadata."""
    uom = (product.unit_of_measure or "").strip()
    qty = product.quantity or 0
    if product.is_weighted:
        return ""
    if not qty or qty <= 0:
        return ""
    # Normalize display
    if "ליטר" in uom.lower():
        if qty < 10:
            return f"{qty:g} ל"
        return f"{qty:g} מ\"ל"
    if "100" in uom and "מילי" in uom.lower():
        return f"{qty:g} מ\"ל"
    if "100" in uom and "גרם" in uom.lower():
        return f"{qty:g} גר"
    if qty >= 1000:
        return f"{qty/1000:g} ק\"ג"
    if qty > 0:
        return f"{qty:g} גר"
    return ""


def _normalize_pkg_size(product: Product) -> float:
    """Return package size in grams or ml."""
    pkg_size = product.quantity or 0
    uom = (product.unit_of_measure or "").lower()
    if pkg_size and "ליטר" in uom and pkg_size < 100:
        return pkg_size * 1000
    if pkg_size and ('ק"ג' in uom or "קילו" in uom) and pkg_size < 100:
        return pkg_size * 1000
    return pkg_size


async def _estimate_cost(api_key: str, ingredient: Ingredient, product: Product) -> CostInfo:
    """Compute cost info for an ingredient/product pair."""
    qty = ingredient.quantity
    unit = ingredient.unit.lower().strip()
    factor = _TO_GRAMS.get(unit)
    desc = _pkg_desc(product)
    note = ""

    # ── Weighted products (price per kg) ──────────────────────────
    if product.is_weighted:
        if factor:
            needed_g = qty * factor
        elif unit == "unit" or not factor:
            # Look up typical weight: exact name → plural-stripped → Claude → fallback
            typical = TYPICAL_WEIGHTS.get(ingredient.name)
            if not typical:
                stripped = ingredient.name.rstrip("ים").rstrip("ות")
                typical = TYPICAL_WEIGHTS.get(stripped)
            if not typical:
                typical = await _estimate_weight_via_claude(api_key, ingredient.name)
                if typical:
                    note = "⚠ משקל משוער"
            if not typical:
                typical = _DEFAULT_UNIT_WEIGHT_G
                note = "⚠ משקל משוער"
            needed_g = qty * typical
        cost = product.price * (needed_g / 1000)
        return CostInfo(
            recipe_cost=cost,
            full_price=product.price,
            pkg_desc="ק\"ג",
            note=note,
        )

    # ── Packaged products — proportional cost ─────────────────────
    pkg_size = _normalize_pkg_size(product)
    if pkg_size and pkg_size > 0 and factor and unit != "unit":
        needed = qty * factor
        if needed < pkg_size:
            ratio = needed / pkg_size
            proportional = product.price * ratio
            is_bulk = pkg_size >= 500
            if not is_bulk and proportional < product.price * 0.2:
                return CostInfo(
                    recipe_cost=product.price,
                    full_price=product.price,
                    pkg_desc=desc,
                    buy_full=True,
                    note=note,
                )
            return CostInfo(
                recipe_cost=proportional,
                full_price=product.price,
                pkg_desc=desc,
                is_partial=True,
                note=note,
            )
        units_needed = needed / pkg_size
        return CostInfo(
            recipe_cost=product.price * units_needed,
            full_price=product.price,
            pkg_desc=desc,
            note=note,
        )

    # ── Unit-price shortcut (per 100g/100ml on label) ─────────────
    if product.unit_price and factor and product.unit_of_measure:
        um = product.unit_of_measure.lower()
        if "100" in um:
            cost = product.unit_price * (qty * factor / 100)
            return CostInfo(
                recipe_cost=cost,
                full_price=product.price,
                pkg_desc=desc,
                is_partial=cost < product.price,
                note=note,
            )

    return CostInfo(
        recipe_cost=product.price,
        full_price=product.price,
        pkg_desc=desc,
        note=note,
    )


async def _estimate_weight_via_claude(api_key: str, ingredient_name: str) -> int | None:
    """Ask Claude for the typical weight of one unit. Cache in TYPICAL_WEIGHTS."""
    try:
        raw = await _call_claude(
            api_key,
            messages=[{"role": "user",
                       "content": f"What is the typical weight in grams of one {ingredient_name}? "
                                  "Reply with just a number, nothing else."}],
            system="You are a food weight reference. Reply with ONLY an integer number of grams. No text.",
            max_tokens=20,
        )
        digits = re.sub(r"[^\d]", "", raw.strip())
        if not digits:
            return None
        grams = int(digits)
        if 1 <= grams <= 50000:
            TYPICAL_WEIGHTS[ingredient_name] = grams
            logger.info(f"Claude estimated weight for '{ingredient_name}': {grams}g")
            return grams
    except Exception as e:
        logger.warning(f"Weight estimation failed for '{ingredient_name}': {e}")
    return None


# ── Claude helpers ─────────────────────────────────────────────────────────────

async def _call_claude(api_key: str, messages: list, system: str, max_tokens=1500) -> str:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system,
        "messages": messages,
    }
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(CLAUDE_API, headers=headers, json=body)
        r.raise_for_status()
        return r.json()["content"][0]["text"]


EXTRACT_SYSTEM = """You are a recipe parser. Extract the ingredients list.
Return ONLY a JSON array, no markdown fences, no explanation.
Each element: {"name": "...", "quantity": number, "unit": "...", "optional": false, "alternatives": [...]}

WEIGHT EXTRACTION RULES (HIGHEST PRIORITY — apply these FIRST):

1. Scan the ENTIRE line for weight patterns. If ANY of these appear ANYWHERE
   in the line (before, after, or across parentheses), use that weight:
     'במשקל X ק"ג' → quantity=X, unit="kg"
     'במשקל X גרם' → quantity=X, unit="g"
     'X ק"ג'        → quantity=X, unit="kg"
     'X גרם'        → quantity=X, unit="g"

2. Content inside parentheses () is NEVER a quantity. Parentheses contain
   alternatives (או X או Y) or descriptions. Skip them for quantity parsing.

3. 'נתח' means 'cut/piece' — it is NOT a quantity. Do not use it.
   '1 נתח' does NOT mean quantity=1 if a weight appears later in the line.

4. When BOTH a count AND a weight appear, the WEIGHT ALWAYS wins:
   '1 נתח שייטל במשקל 2 ק"ג' → quantity=2, unit="kg" (NOT quantity=1)
   '2 חזות עוף במשקל 600 גרם' → quantity=600, unit="g" (NOT quantity=2)

ALTERNATIVES RULE: When a recipe says "או" (or) between options like
"שייטל (או חזה או כתף בקר)", extract ONLY the FIRST option as "name".
Put the other options in the "alternatives" array as clean ingredient nouns.

NAME RULE: The "name" field must contain ONLY the ingredient noun.
Strip measurement words: כפות, כפית, כוס, חבילה, סלסילה, גרם, ק"ג, נתח, שן, שיני, etc.
Strip descriptors: חתוכים לרבעים, קלופות, טחון, מגורד, מופרדות מהבסיס, יבש, גרוס, גרגרים

Examples of CORRECT extraction:
  "1 נתח שייטל (או חזה או כתף בקר) במשקל 2 ק\\"ג"
    → {"name": "שייטל", "quantity": 2, "unit": "kg", "alternatives": ["חזה בקר", "כתף בקר"]}

  "500 גרם פרגיות (או חזה עוף)"
    → {"name": "פרגיות", "quantity": 500, "unit": "g", "alternatives": ["חזה עוף"]}

  "4 כפות שמן זית" → {"name": "שמן זית", "quantity": 4, "unit": "tbsp", "alternatives": []}
  "2 כפות סילאן" → {"name": "סילאן", "quantity": 2, "unit": "tbsp", "alternatives": []}
  "3/4 כוס יין לבן יבש" → {"name": "יין לבן", "quantity": 0.75, "unit": "cup", "alternatives": []}
  "6 שיני שום קלופות" → {"name": "שום", "quantity": 6, "unit": "unit", "alternatives": []}
  "מלח ופלפל שחור גרוס" → two entries: "מלח" and "פלפל שחור", each with "alternatives": []
  "שייטל 2 ק\\"ג" → {"name": "שייטל", "quantity": 2, "unit": "kg", "alternatives": []}
  "1 סלסילה פטריות שמפיניון חתוכות לרבעים"
    → {"name": "פטריות שמפיניון", "quantity": 1, "unit": "סלסילה", "alternatives": []}
  "1 חבילה פטריות שימגי מופרדות מהבסיס"
    → {"name": "פטריות שימגי", "quantity": 1, "unit": "חבילה", "alternatives": []}
  "4 פטריות פורטובלו חתוכות לרבעים"
    → {"name": "פטריות פורטובלו", "quantity": 4, "unit": "unit", "alternatives": []}
  "2 כפות חרדל דיז'ון גרגרים"
    → {"name": "חרדל דיז'ון", "quantity": 2, "unit": "tbsp", "alternatives": []}
  "4 בצלים חתוכים לרבעים"
    → {"name": "בצלים", "quantity": 4, "unit": "unit", "alternatives": []}

Hebrew ingredient synonyms:
  סילאן = date honey/syrup, NOT סיליקון
  שמפיניון = champignon mushrooms
  שימגי/שימג'י = shimeji mushrooms
  פורטובלו = portobello mushrooms

QUANTITY RULES:
- Never return quantity=0. Minimum is 0.1.
- Fractions: 1/2=0.5, 1/4=0.25, 3/4=0.75, 1 1/2=1.5 (pre-converted in input)
- If quantity is truly absent, use 1 as default.
- Imperial units: oz, lb, fl oz are valid units. Keep them as-is.
  "15 oz ricotta" → quantity=15, unit="oz"
  "1 pound ground beef" → quantity=1, unit="lb"
  "0.25 lb parmesan" → quantity=0.25, unit="lb"

For unit items (onion, egg, whole mushroom) use unit="unit".
If quantity is vague (pinch, to taste) estimate a typical small amount.
If no quantity given for vegetables, estimate ~200g."""


def _normalize_fractions(text: str) -> str:
    """Convert fraction strings to decimals before Claude sees them."""
    # "1 1/2" → "1.5"
    text = re.sub(
        r'(\d+)\s+(\d+)/(\d+)',
        lambda m: str(round(int(m.group(1)) + int(m.group(2)) / int(m.group(3)), 3)),
        text,
    )
    # "1/2" → "0.5"
    text = re.sub(
        r'\b(\d+)/(\d+)\b',
        lambda m: str(round(int(m.group(1)) / int(m.group(2)), 3)),
        text,
    )
    return text


async def extract_from_text(api_key: str, recipe_text: str) -> list[Ingredient]:
    recipe_text = _normalize_fractions(recipe_text)
    raw = await _call_claude(api_key,
        messages=[{"role": "user", "content": recipe_text}],
        system=EXTRACT_SYSTEM,
    )
    return _parse_ingredients_json(raw)


async def extract_from_image(api_key: str, image_b64: str) -> list[Ingredient]:
    messages = [{
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": image_b64,
                }
            },
            {
                "type": "text",
                "text": "This is a recipe image. Extract the ingredients list."
            }
        ]
    }]
    raw = await _call_claude(api_key, messages=messages, system=EXTRACT_SYSTEM, max_tokens=1500)
    return _parse_ingredients_json(raw)


def _parse_ingredients_json(raw: str) -> list[Ingredient]:
    raw = re.sub(r"```json|```", "", raw).strip()
    try:
        items = json.loads(raw)
        results = []
        for i in items:
            qty = float(i.get("quantity", 1))
            if qty <= 0:
                qty = 1  # never allow zero/negative
            results.append(Ingredient(
                name=i["name"],
                quantity=qty,
                unit=i.get("unit", "unit"),
                optional=i.get("optional", False),
                alternatives=[str(a) for a in i.get("alternatives", []) if a],
            ))
        return results
    except Exception as e:
        logger.error(f"Failed to parse ingredients JSON: {e}\nRaw: {raw}")
        return []


# ── Product matching ───────────────────────────────────────────────────────────

def _has_hebrew(text: str) -> bool:
    return any('\u0590' <= c <= '\u05FF' for c in text)


async def _translate_en_to_he(api_key: str, name: str) -> str:
    """Translate English ingredient to Hebrew. Dict first, Claude fallback."""
    translated = translate_to_hebrew(name)
    if _has_hebrew(translated):
        return translated
    # Claude fallback for unknown English ingredients
    try:
        raw = await _call_claude(
            api_key,
            messages=[{"role": "user",
                       "content": f"Translate this ingredient to Hebrew: {name}\n"
                                  "Reply with ONLY the Hebrew name, nothing else."}],
            system="You translate food ingredient names to Hebrew. Reply with ONLY the Hebrew name.",
            max_tokens=30,
        )
        result = raw.strip()
        if _has_hebrew(result):
            logger.info(f"Claude translated '{name}' → '{result}'")
            return result
    except Exception as e:
        logger.warning(f"Translation failed for '{name}': {e}")
    return name


async def match_ingredient(api_key: str, ingredient: Ingredient, db: ProductDB) -> MatchedIngredient:
    all_names = db.all_names()
    if not all_names:
        return MatchedIngredient(ingredient=ingredient, matches=[], not_found=True)

    names_list = [name for _, name in all_names]
    name_to_id = {name: row_id for row_id, name in all_names}

    # Translate English ingredients to Hebrew for fuzzy matching
    search_name = ingredient.name
    if not _has_hebrew(search_name):
        search_name = await _translate_en_to_he(api_key, search_name)

    translated_alts = []
    for alt in ingredient.alternatives:
        if _has_hebrew(alt):
            translated_alts.append(alt)
        else:
            translated_alts.append(await _translate_en_to_he(api_key, alt))

    # Search for main ingredient + alternatives
    search_terms = [search_name] + translated_alts
    seen_names: dict[str, tuple[str, float, int]] = {}
    for term in search_terms:
        for match_name, score, idx in process.extract(
            term, names_list, scorer=fuzz.token_set_ratio, limit=FUZZY_TOP_N
        ):
            if match_name not in seen_names or score > seen_names[match_name][1]:
                seen_names[match_name] = (match_name, score, idx)
        for match_name, score, idx in process.extract(
            term, names_list, scorer=fuzz.partial_ratio, limit=FUZZY_TOP_N
        ):
            if match_name not in seen_names or score > seen_names[match_name][1]:
                seen_names[match_name] = (match_name, score, idx)
    merged = sorted(seen_names.values(), key=lambda x: x[1], reverse=True)[:FUZZY_TOP_N * 2]

    candidate_products = []
    for match_name, score, _ in merged:
        row_id = name_to_id.get(match_name)
        if row_id is not None:
            p = db.get(row_id)
            if p:
                candidate_products.append((score, p))

    if not candidate_products:
        return MatchedIngredient(ingredient=ingredient, matches=[], not_found=True)

    products_text = "\n".join(
        f"{i+1}. {p.name} | {p.manufacturer} | {p.price_display()}"
        for i, (_, p) in enumerate(candidate_products)
    )

    alt_hint = ""
    if ingredient.alternatives:
        alt_names = ", ".join(ingredient.alternatives)
        alt_hint = (
            f"\n\nThe recipe also mentions these as alternatives: {alt_names}. "
            f"Pick the best match for '{ingredient.name}' as #1, then try to find "
            f"matches for the alternatives as #2, #3 so the user can compare prices."
        )

    system = f"""You are a grocery matching assistant.
Select the {SHOW_ALTS + 1} most suitable products for the ingredient, ranked best first.
Return ONLY a JSON array of 1-based indices like [3,1,7]. No explanation.

IMPORTANT: Reject matches where the product is from a completely different food category.
Examples of forbidden cross-category matches:
- vegetable ingredient → meat/poultry product
- spice/condiment → fresh produce
- liquid ingredient → solid snack food
- raw ingredient → unrelated processed product (e.g. בצל → ביסלי בטעם בצל)

However, DO accept:
- Processed/packaged versions of the same ingredient (e.g. בצל → בצל קוביות פרוזן, בצל פנינה)
- Different forms of the same food (fresh, frozen, canned, dried)

If no candidate is even close, return [] (empty array)."""

    user = (
        f"Need: {ingredient.name} ({ingredient.quantity} {ingredient.unit})"
        f"\n\nCandidates:\n{products_text}"
        f"{alt_hint}"
    )

    try:
        raw = await _call_claude(api_key,
            messages=[{"role": "user", "content": user}],
            system=system, max_tokens=100
        )
        raw = re.sub(r"```json|```", "", raw).strip()
        indices = json.loads(raw)
        chosen = [candidate_products[i-1][1] for i in indices if 1 <= i <= len(candidate_products)]
    except Exception:
        chosen = []

    if not chosen:
        return MatchedIngredient(ingredient=ingredient, matches=[], not_found=True)

    chosen = chosen[:SHOW_ALTS + 1]
    best = chosen[0]
    # Use translated name for quality check (English names have no Hebrew chars)
    suspicious = _check_match_quality(search_name, best.name)
    zero_price = best.price <= 0
    return MatchedIngredient(
        ingredient=ingredient, matches=chosen,
        suspicious=suspicious, zero_price=zero_price,
    )


# ── Display helpers ────────────────────────────────────────────────────────────

_FRACTION_SYMS = {0.25: "¼", 0.33: "⅓", 0.5: "½", 0.67: "⅔", 0.75: "¾"}

def _fraction_display(q: float) -> str:
    for val, sym in _FRACTION_SYMS.items():
        if abs(q - val) < 0.05:
            return sym
    return str(int(q)) if q == int(q) else f"{q:.1f}"


_IMPERIAL_TO_METRIC = {
    "oz": (28.35, "גר"), "ounce": (28.35, "גר"), "ounces": (28.35, "גר"),
    "lb": (453.59, "גר"), "lbs": (453.59, "גר"),
    "pound": (453.59, "גר"), "pounds": (453.59, "גר"),
    "fl oz": (29.57, 'מ"ל'), "fluid oz": (29.57, 'מ"ל'),
    "cup": (240, 'מ"ל'), "cups": (240, 'מ"ל'),
    "tbsp": (15, 'מ"ל'), "tablespoon": (15, 'מ"ל'), "tablespoons": (15, 'מ"ל'),
    "tsp": (5, 'מ"ל'), "teaspoon": (5, 'מ"ל'), "teaspoons": (5, 'מ"ל'),
    "pinch": (0.5, "גר"),
    "כוס": (240, 'מ"ל'), "כוסות": (240, 'מ"ל'),
    "כף": (15, 'מ"ל'), "כפות": (15, 'מ"ל'),
    "כפית": (5, 'מ"ל'), "כפיות": (5, 'מ"ל'),
    "קורט": (0.5, "גר"),
}


def normalize_display(quantity: float, unit: str) -> tuple[float, str]:
    """Convert to metric for display. Returns (metric_qty, metric_unit)."""
    unit_lower = unit.lower().strip()
    if unit_lower in _IMPERIAL_TO_METRIC:
        factor, metric_unit = _IMPERIAL_TO_METRIC[unit_lower]
        result = quantity * factor
        if metric_unit == "גר" and result >= 1000:
            return round(result / 1000, 2), 'ק"ג'
        if metric_unit == 'מ"ל' and result >= 1000:
            return round(result / 1000, 2), "ל׳"
        return round(result, 1), metric_unit
    # Already metric
    METRIC_LABELS = {
        "g": "גר", "gr": "גר", "gram": "גר", "grams": "גר", "גרם": "גר",
        "kg": 'ק"ג', 'ק"ג': 'ק"ג', "קג": 'ק"ג', "kilogram": 'ק"ג',
        "ml": 'מ"ל', "מל": 'מ"ל', "milliliter": 'מ"ל',
        "l": "ל׳", "ליטר": "ל׳", "liter": "ל׳", "litre": "ל׳",
    }
    if unit_lower in METRIC_LABELS:
        return quantity, METRIC_LABELS[unit_lower]
    return quantity, unit


def smart_round_qty(val: float, unit: str) -> str:
    """Round displayed quantity for readability."""
    if unit in ("גר", 'מ"ל'):
        if val < 10:
            return f"{val:.1f}"
        if val < 100:
            return f"{round(val)}"
        return f"{round(val / 5) * 5}"
    if unit in ('ק"ג', "ל׳"):
        return f"{val:.2f}".rstrip("0").rstrip(".")
    return f"{val:.0f}" if val == int(val) else f"{val:.1f}"


def _format_qty(ing: Ingredient) -> str:
    """Format ingredient quantity for Telegram display — always metric."""
    unit_lower = ing.unit.lower().strip()
    if unit_lower == "unit":
        frac = _fraction_display(ing.quantity)
        return f"{frac} יח׳"
    metric_qty, metric_unit = normalize_display(ing.quantity, ing.unit)
    return f"{smart_round_qty(metric_qty, metric_unit)} {metric_unit}"


def _format_unit_weight_display(ing: Ingredient) -> str:
    """For unit items on weighted products: '~75 גר (½ יחידה)'."""
    typical = TYPICAL_WEIGHTS.get(ing.name)
    if not typical:
        stripped = ing.name.rstrip("ים").rstrip("ות")
        typical = TYPICAL_WEIGHTS.get(stripped, _DEFAULT_UNIT_WEIGHT_G)
    weight_g = typical * ing.quantity
    frac = _fraction_display(ing.quantity)
    unit_word = "יחידה" if ing.quantity <= 1 else "יחידות"
    return f"~{smart_round_qty(weight_g, 'גר')} גר ({frac} {unit_word})"


def _format_price_col(ci: CostInfo) -> str:
    """Format the package price column."""
    if ci.is_partial:
        return f"₪{ci.recipe_cost:.2f} מתוך ₪{ci.full_price:.2f}"
    if ci.buy_full:
        return f"₪{ci.full_price:.2f} (אריזה שלמה)"
    return f"₪{ci.full_price:.2f}"


async def _format_table(api_key: str, matched: list[MatchedIngredient]) -> str:
    """Rich HTML comparison table for Telegram."""
    sep = "——————————————————"
    lines = [sep, "🛒 <b>קשת טעמים — חישוב עלות מתכון</b>", sep]

    recipe_total = 0.0
    purchase_total = 0.0
    purchase_items: list[str] = []
    warnings: list[str] = []
    not_found_items: list[str] = []

    # Track the most expensive meat alternative for savings calc
    meat_alts: list[tuple[str, float]] = []  # (name, recipe_total_with_this)
    meat_best_cost = 0.0
    non_meat_total = 0.0

    # First pass: compute costs
    cost_infos: list[Optional[CostInfo]] = []
    alt_cost_infos: list[list[CostInfo]] = []
    for m in matched:
        if m.not_found or not m.best() or m.zero_price:
            cost_infos.append(None)
            alt_cost_infos.append([])
            continue
        ci = await _estimate_cost(api_key, m.ingredient, m.best())
        m.cost_info = ci
        cost_infos.append(ci)
        # Compute alt costs
        alts_ci = []
        for alt in m.matches[1:]:
            aci = await _estimate_cost(api_key, m.ingredient, alt)
            alts_ci.append(aci)
        m.alt_costs = alts_ci
        alt_cost_infos.append(alts_ci)

    # Second pass: render
    for idx, m in enumerate(matched):
        ing = m.ingredient
        best = m.best()
        ci = cost_infos[idx]
        emoji = _emoji_for(ing.name)
        # For unit items on weighted products, show gram estimate
        if (best and best.is_weighted and ing.unit.lower().strip() == "unit"):
            qty_str = _format_unit_weight_display(ing)
        else:
            qty_str = _format_qty(ing)
        opt_mark = " *" if ing.optional else ""

        # ── Not found ─────────────────────────────────────
        if m.not_found or not best:
            not_found_items.append(ing.name)
            lines.append(f"{emoji} <b>{ing.name}</b> {qty_str}{opt_mark}")
            lines.append(f"   ❓ <i>לא נמצא במאגר</i>")
            lines.append("")
            continue

        # ── Zero price ────────────────────────────────────
        if m.zero_price:
            warnings.append(f"🚫 {ing.name} — מחיר חסר")
            lines.append(f"{emoji} <b>{ing.name}</b> {qty_str}{opt_mark}")
            lines.append(f"   🚫 {best.name[:25]} — <i>מחיר חסר</i>")
            lines.append("")
            continue

        # ── Normal match ──────────────────────────────────
        # Sanity: flag absurd costs from DB data errors (e.g. pkg_size=1g)
        best_pkg = _normalize_pkg_size(best)
        if best_pkg and 0 < best_pkg < 5 and ci.recipe_cost > 50:
            ci.note = "⚠ מחיר חריג"
        if m.suspicious:
            warnings.append(f"⚠ {ing.name} → {best.name} — התאמה חשודה")
        if ci.note and ci.note.startswith("⚠"):
            warnings.append(f"{ci.note}: {ing.name}")

        cost = ci.recipe_cost
        if not ing.optional:
            recipe_total += cost

        # Track purchase cost (full package)
        pkg_buy = ci.full_price if (ci.is_partial or ci.buy_full) else cost
        if ci.is_partial or ci.buy_full:
            purchase_total += ci.full_price
            purchase_items.append(
                f"   {best.name[:22]}: <b>₪{ci.full_price:.2f}</b>"
                + (f" ({ci.pkg_desc})" if ci.pkg_desc else "")
            )
        else:
            purchase_total += cost
            purchase_items.append(
                f"   {best.name[:22]}: <b>₪{cost:.2f}</b>"
                + (f" ({ci.pkg_desc})" if ci.pkg_desc else "")
            )

        # Track meat alternatives for savings
        is_meat = emoji == "🥩"
        if is_meat:
            meat_best_cost = cost
            meat_alts.append((best.name[:18], cost))
            for i, alt in enumerate(m.matches[1:]):
                if i < len(m.alt_costs):
                    meat_alts.append((alt.name[:18], m.alt_costs[i].recipe_cost))
        else:
            non_meat_total += cost

        # Format ingredient row
        price_col = _format_price_col(ci)
        note_str = f" {ci.note}" if ci.note else ""
        product_short = best.name[:22]

        lines.append(f"{emoji} <b>{ing.name}</b> {qty_str}{opt_mark}")
        lines.append(
            f"   {product_short} | <b>₪{cost:.2f}</b> | {price_col}{note_str}"
        )

        # Alternatives with savings (skip absurd prices from DB data errors)
        for i, alt in enumerate(m.matches[1:]):
            if i >= len(m.alt_costs):
                break
            aci = m.alt_costs[i]
            # Sanity: if recipe cost > ₪200/100g equivalent, skip (DB data error)
            pkg_sz = _normalize_pkg_size(alt)
            if pkg_sz and 0 < pkg_sz < 5 and aci.recipe_cost > 50:
                continue  # likely DB error: weight stored as 1g
            if aci.recipe_cost > cost * 10 and aci.recipe_cost > 100:
                continue  # absurd cost, skip
            alt_short = alt.name[:20]
            saving = cost - aci.recipe_cost
            saving_str = f" ← חסכון ₪{saving:.0f}" if saving > 1 else ""
            lines.append(
                f"   <i>↳ {alt_short} | ₪{aci.recipe_cost:.2f} | "
                f"{aci.full_price:.2f}/{aci.pkg_desc or 'יח'}{saving_str}</i>"
            )

        lines.append("")

    # ── Summary ───────────────────────────────────────────
    lines.append(sep)

    # Main total with selected meat
    if meat_alts:
        selected_name = meat_alts[0][0]
        lines.append(f"💰 <b>סה״כ למנה (עם {selected_name}): ₪{recipe_total:.2f}</b>")
    else:
        lines.append(f"💰 <b>סה״כ למנה: ₪{recipe_total:.2f}</b>")

    # Meat alternative totals
    if len(meat_alts) > 1:
        for name, alt_cost in meat_alts[1:]:
            alt_total = non_meat_total + alt_cost
            diff = recipe_total - alt_total
            if abs(diff) > 1:
                if diff > 0:
                    lines.append(
                        f"💡 עם {name}: ₪{alt_total:.2f} (חסכון ₪{diff:.0f})"
                    )
                else:
                    lines.append(
                        f"💡 עם {name}: ₪{alt_total:.2f} (+₪{-diff:.0f})"
                    )

    lines.append(sep)

    # Purchase breakdown
    if purchase_items:
        lines.append("📦 <b>רכישות נדרשות (מה באמת קונים):</b>")
        for item in purchase_items:
            lines.append(item)
        lines.append(f"   <b>סה״כ קניה בפועל: ₪{purchase_total:.2f}</b>")
        lines.append(sep)

    # Warnings
    if warnings or not_found_items:
        lines.append("")
        lines.append("<b>⚠ שים לב:</b>")
        for w in warnings:
            lines.append(f"  {w}")
        for nf in not_found_items:
            lines.append(f"  ❓ {nf} — לא נמצא במאגר")

    if any(m.ingredient.optional for m in matched):
        lines.append("")
        lines.append("<i>* מרכיבים אופציונליים לא נכללים בסכום</i>")

    return "\n".join(lines)


# ── Main entry point ───────────────────────────────────────────────────────────

class RecipePricer:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.db = ProductDB()

    async def _price(self, ingredients: list[Ingredient]) -> tuple[str, list[MatchedIngredient]]:
        if not ingredients:
            return "❌ לא הצלחתי לחלץ מרכיבים. נסה שוב עם מתכון מפורט יותר.", []
        matched = [await match_ingredient(self.api_key, ing, self.db) for ing in ingredients]
        text = await _format_table(self.api_key, matched)
        return text, matched

    async def price_recipe_text(self, recipe_text: str) -> tuple[str, list[MatchedIngredient]]:
        if self.db.is_empty():
            return "❌ מסד הנתונים ריק. טען קבצי מחירים תחילה.", []
        ingredients = await extract_from_text(self.api_key, recipe_text)
        return await self._price(ingredients)

    async def price_recipe_image(self, image_b64: str) -> tuple[str, list[MatchedIngredient]]:
        if self.db.is_empty():
            return "❌ מסד הנתונים ריק. טען קבצי מחירים תחילה.", []
        ingredients = await extract_from_image(self.api_key, image_b64)
        return await self._price(ingredients)
