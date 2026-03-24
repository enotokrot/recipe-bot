"""
HTML report generator for recipe price analysis.
Uses the template CSS from recipe_report_template.html, builds body dynamically.
"""

import html
from pathlib import Path
from datetime import datetime
from matcher import (
    MatchedIngredient, CostInfo, _normalize_pkg_size, _TO_GRAMS,
    _emoji_for, normalize_display, smart_round_qty, _fraction_display,
    TYPICAL_WEIGHTS, _DEFAULT_UNIT_WEIGHT_G,
)


def _esc(text: str) -> str:
    return html.escape(str(text))


def _metric_display(qty: float, unit: str) -> str:
    """Convert to metric display string."""
    unit_lower = unit.lower().strip()
    if unit_lower == "unit":
        return ""
    mq, mu = normalize_display(qty, unit)
    return f"{smart_round_qty(mq, mu)} {mu}"


def _unit_weight_display(ing_name: str, qty: float) -> str:
    """For unit items: '~75 גר (½ יחידה)'."""
    typical = TYPICAL_WEIGHTS.get(ing_name)
    if not typical:
        stripped = ing_name.rstrip("ים").rstrip("ות")
        typical = TYPICAL_WEIGHTS.get(stripped, _DEFAULT_UNIT_WEIGHT_G)
    weight_g = typical * qty
    frac = _fraction_display(qty)
    unit_word = "יחידה" if qty <= 1 else "יחידות"
    return f"~{smart_round_qty(weight_g, 'גר')} גר ({frac} {unit_word})"


def _cost_note_html(ci: CostInfo) -> str:
    if ci.is_partial:
        return f'<div class="cost-of">מתוך ₪{ci.full_price:.2f}</div>'
    if ci.buy_full:
        return '<div class="cost-of"><span class="badge badge-full">אריזה שלמה</span></div>'
    if ci.note:
        return f'<div class="cost-of"><span class="badge badge-warn">{_esc(ci.note)}</span></div>'
    return ""


def _load_template_css() -> str:
    """Extract CSS from the template file."""
    tmpl_path = Path(__file__).parent / "recipe_report_template.html"
    if tmpl_path.exists():
        content = tmpl_path.read_text(encoding="utf-8")
        start = content.find("<style>")
        end = content.find("</style>")
        if start >= 0 and end >= 0:
            return content[start:end + len("</style>")]
    # Fallback minimal CSS
    return "<style>body{font-family:Heebo,sans-serif;direction:rtl;}</style>"


def generate_html_report(
    matched: list[MatchedIngredient],
    recipe_name: str = "מתכון",
    servings: int = 4,
    prep_time: str = "",
    cook_time: str = "",
) -> str:
    """Generate a self-contained HTML report from matched ingredients."""

    css = _load_template_css()

    # Compute totals and build rows
    recipe_total = 0.0
    purchase_total = 0.0
    matched_count = 0
    warning_count = 0
    rows_html = []
    warnings_html = []

    for m in matched:
        ing = m.ingredient
        best = m.best()
        ci = m.cost_info
        emoji = _emoji_for(ing.name)

        # Quantity display — always metric
        unit_lower = ing.unit.lower().strip()
        if unit_lower == "unit":
            if best and best.is_weighted:
                qty_display_full = _unit_weight_display(ing.name, ing.quantity)
                metric_col = qty_display_full.split("(")[0].strip()  # "~75 גר"
            else:
                frac = _fraction_display(ing.quantity)
                qty_display_full = f"{frac} יח׳"
                metric_col = qty_display_full
        else:
            metric = _metric_display(ing.quantity, ing.unit)
            qty_display_full = metric
            metric_col = metric

        optional_badge = '<span class="badge badge-opt">אופציונלי</span>' if ing.optional else ""

        # ── Not found ──
        if m.not_found or not best:
            warning_count += 1
            warnings_html.append(
                f'<div class="warning-item"><span class="w-icon">❓</span>'
                f'<span><strong>{_esc(ing.name)}</strong> — לא נמצא במאגר</span></div>'
            )
            rows_html.append(f"""<tr>
  <td>
    <div class="ing-name">{emoji} {_esc(ing.name)} {optional_badge}</div>
    <div class="ing-qty">{_esc(qty_display_full)}</div>
  </td>
  <td colspan="4" style="color:var(--text3);font-style:italic">❓ לא נמצא במאגר</td>
</tr>""")
            continue

        # ── Zero price ──
        if m.zero_price:
            warning_count += 1
            rows_html.append(f"""<tr>
  <td>
    <div class="ing-name">{emoji} {_esc(ing.name)} {optional_badge}</div>
    <div class="ing-qty">{_esc(qty_display_full)}</div>
  </td>
  <td><div class="product-name">{_esc(best.name)}</div></td>
  <td class="num">—</td>
  <td class="num"><span style="color:var(--red)">מחיר חסר</span></td>
  <td class="num">—</td>
</tr>""")
            continue

        matched_count += 1
        cost = ci.recipe_cost if ci else 0
        if not ing.optional:
            recipe_total += cost

        if ci and (ci.is_partial or ci.buy_full):
            purchase_total += ci.full_price
        else:
            purchase_total += cost

        if m.suspicious:
            warning_count += 1
            warnings_html.append(
                f'<div class="warning-item"><span class="w-icon">⚠</span>'
                f'<span><strong>{_esc(ing.name)}</strong> → {_esc(best.name)} — התאמה חשודה</span></div>'
            )
        if ci and ci.note and "משוער" in ci.note:
            warnings_html.append(
                f'<div class="warning-item"><span class="w-icon">⚠</span>'
                f'<span><strong>{_esc(ing.name)}</strong> — {_esc(ci.note)}</span></div>'
            )

        # Package info
        price_per = _esc(best.price_display())
        pkg_desc = _esc(ci.pkg_desc) if ci else ""
        cost_note = _cost_note_html(ci) if ci else ""

        # Alternatives
        alts_html = ""
        for i, alt in enumerate(m.matches[1:]):
            if i >= len(m.alt_costs):
                break
            aci = m.alt_costs[i]
            pkg_sz = _normalize_pkg_size(alt)
            if pkg_sz and 0 < pkg_sz < 5 and aci.recipe_cost > 50:
                continue
            if aci.recipe_cost > cost * 10 and aci.recipe_cost > 100:
                continue
            saving = cost - aci.recipe_cost
            saving_html = (
                f'<span class="save-badge">חסכון ₪{saving:.0f}</span> '
                if saving > 1 else ""
            )
            alts_html += (
                f'<div class="alt-item"><span class="alt-arrow">↳</span> '
                f'{saving_html}{_esc(alt.name[:30])} — ₪{aci.recipe_cost:.2f}</div>'
            )

        # Assumed weight badge
        assumed = ""
        if ci and ci.note and "משוער" in ci.note:
            assumed = ' <span class="badge badge-warn">משוער</span>'

        # Build qty display with arrow for non-metric original
        orig_unit = ing.unit.lower().strip()
        if orig_unit in ("unit",) and best and best.is_weighted:
            qty_line = f'{_esc(qty_display_full)}{assumed}'
        elif orig_unit in ("g", "gr", "kg", "ml", "l", "גרם", 'ק"ג', "מל", "ליטר"):
            qty_line = f'{_esc(qty_display_full)}'
        else:
            # Show original → metric
            mq, mu = normalize_display(ing.quantity, ing.unit)
            qty_line = (
                f'{ing.quantity:g} {ing.unit} '
                f'<span class="arrow">→</span> '
                f'<span class="qty-metric">{smart_round_qty(mq, mu)} {mu}</span>{assumed}'
            )

        opt_style = ' style="opacity:0.6"' if ing.optional else ""

        rows_html.append(f"""<tr{opt_style}>
  <td>
    <div class="ing-name">{emoji} {_esc(ing.name)} {optional_badge}</div>
    <div class="ing-qty">{qty_line}</div>
  </td>
  <td>
    <div class="product-name">{_esc(best.name)}</div>
    <div class="product-alts">{alts_html}</div>
  </td>
  <td class="num"><div class="cost-main">{_esc(metric_col)}</div></td>
  <td class="num">
    <div class="cost-main">₪{cost:.2f}</div>
    {cost_note}
  </td>
  <td class="num">
    <div class="pkg-price">{price_per}</div>
    <div class="pkg-size">{pkg_desc}</div>
  </td>
</tr>""")

    per_serving = recipe_total / servings if servings > 0 else recipe_total

    # Meta line
    meta_parts = []
    if prep_time:
        meta_parts.append(f'<span>⏱ הכנה: {_esc(prep_time)}</span>')
    if cook_time:
        meta_parts.append(f'<span>🍳 בישול: {_esc(cook_time)}</span>')
    if servings:
        meta_parts.append(f'<span>🍽 מנות: {servings}</span>')
    meta_html = "\n      ".join(meta_parts)

    warnings_section = ""
    if warnings_html:
        warnings_section = f"""
  <div class="warnings">
    <div class="warnings-title">⚠ שים לב</div>
    {''.join(warnings_html)}
  </div>"""

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(recipe_name)} — דוח מחירים</title>
<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600&family=Frank+Ruhl+Libre:wght@400;700&display=swap" rel="stylesheet">
{css}
</head>
<body>
<div class="page">

  <div class="header">
    <div class="brand">
      <div class="brand-logo">ק</div>
      <div>
        <div class="brand-name">קשת טעמים</div>
        <div class="brand-sub">הערכת עלות מתכון</div>
      </div>
    </div>
    <div class="header-meta">
      <div id="report-date"></div>
      <div>סניף: כל הסניפים</div>
    </div>
  </div>

  <div class="recipe-title">
    <h1>{_esc(recipe_name)}</h1>
    <div class="meta">
      {meta_html}
    </div>
  </div>

  <div class="summary-cards">
    <div class="card">
      <div class="card-label">מרכיבים</div>
      <div class="card-value">{len(matched)}</div>
      <div class="card-sub">במתכון</div>
    </div>
    <div class="card">
      <div class="card-label">נמצאו</div>
      <div class="card-value green">{matched_count}</div>
      <div class="card-sub">התאמות</div>
    </div>
    <div class="card">
      <div class="card-label">עלות קנייה</div>
      <div class="card-value">₪{purchase_total:.0f}</div>
      <div class="card-sub">אריזות שלמות</div>
    </div>
    <div class="card">
      <div class="card-label">למנה</div>
      <div class="card-value green">₪{per_serving:.0f}</div>
      <div class="card-sub">מתוך {servings} מנות</div>
    </div>
  </div>

  <div class="section">
    <div class="section-header">
      <span class="section-label">מרכיבים</span>
      <div class="section-line"></div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th style="width:22%">מרכיב</th>
            <th style="width:30%">מוצר</th>
            <th class="num" style="width:13%">כמות</th>
            <th class="num" style="width:13%">עלות</th>
            <th class="num" style="width:22%">מחיר אריזה</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
  </div>

  <div class="totals-section">
    <div class="totals-title">סיכום עלויות</div>
    <div class="total-row">
      <span>עלות שימוש בפועל (חלקי אריזות)</span>
      <span>₪{recipe_total:.2f}</span>
    </div>
    <div class="total-row">
      <span>סה"כ קניה (אריזות שלמות)</span>
      <span>₪{purchase_total:.2f}</span>
    </div>
    <div class="total-row main">
      <div>
        <div>סה"כ משוער</div>
        <div class="label-sub">{servings} מנות · ₪{per_serving:.2f} למנה</div>
      </div>
      <span>₪{purchase_total:.2f}</span>
    </div>
  </div>

  {warnings_section}

  <div class="footer">
    <span>נוצר על ידי Recipe Price Bot · קשת טעמים</span>
    <span id="footer-date"></span>
  </div>

</div>

<script>
  const now = new Date();
  const fmt = new Intl.DateTimeFormat('he-IL', {{day:'numeric',month:'long',year:'numeric',hour:'2-digit',minute:'2-digit'}});
  document.getElementById('report-date').textContent = fmt.format(now);
  document.getElementById('footer-date').textContent = fmt.format(now);
</script>
</body>
</html>"""
