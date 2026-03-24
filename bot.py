"""
Recipe Price Bot
Handles both text recipes and recipe images.
Sends back a beautifully formatted table with ingredient matches and total cost.
"""

import base64
import logging
import os
import tempfile
import httpx
from pathlib import Path

# Load .env file if it exists (pip install python-dotenv not required)
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
)
from matcher import RecipePricer
from report_generator import generate_html_report
from catalog import browse_category, search_products, format_product_list, CATEGORY_KEYWORDS
from sync import load_local_file, start_scheduler, sync_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

pricer = RecipePricer(ANTHROPIC_API_KEY)

# ── Startup ────────────────────────────────────────────────────────────────────
# Option A — local files (dev/test):
#   export PRICE_FILES=Price7290785400000-318-*.gz
PRICE_FILES = os.environ.get("PRICE_FILES", "").split(",")
for pf in PRICE_FILES:
    pf = pf.strip()
    if pf:
        load_local_file(pricer.db, pf)

# Option B — live sync from publishedprices.co.il (production):
#   export AUTO_SYNC=1
if os.environ.get("AUTO_SYNC", "0") == "1":
    start_scheduler(pricer.db)   # initial sync + every 30 min


# ── Handlers ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👨‍🍳 *Recipe Price Bot — קשת טעמים*\n\n"
        "📝 *שלח טקסט* — הדבק מתכון בעברית או אנגלית\n"
        "📸 *שלח תמונה* — צלם מתכון, אמצא את המרכיבים ואחשב עלות\n\n"
        "I'll find every ingredient at Keshet Teamim and show you the cost breakdown.",
        parse_mode="Markdown"
    )


async def _send_html_report(update, matched, recipe_name="מתכון"):
    """Generate and send HTML report as a document attachment."""
    try:
        html_content = generate_html_report(matched, recipe_name=recipe_name)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html_content)
            tmp_path = f.name
        safe_name = recipe_name[:30].replace(" ", "_") + "_price_report.html"
        with open(tmp_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=safe_name,
                caption="📊 דוח מחירים מלא — פתח בדפדפן",
            )
        os.unlink(tmp_path)
    except Exception as e:
        logger.warning(f"HTML report generation failed: {e}")


async def handle_text_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recipe_text = update.message.text
    if len(recipe_text) < 10:
        await update.message.reply_text("📝 Please send a full recipe.")
        return
    msg = await update.message.reply_text("🔍 מנתח מתכון...")
    try:
        result, matched = await pricer.price_recipe_text(recipe_text)
        await msg.edit_text(result, parse_mode="HTML")
        if matched:
            await _send_html_report(update, matched, recipe_name=recipe_text[:40])
    except Exception as e:
        logger.error(f"Text recipe error: {e}", exc_info=True)
        await msg.edit_text(f"❌ שגיאה: {e}")


async def handle_image_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📸 מנתח תמונת מתכון...")
    try:
        # Get the highest-res photo Telegram provides
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        # Download image bytes
        async with httpx.AsyncClient() as client:
            resp = await client.get(file.file_path)
            image_bytes = resp.content

        image_b64 = base64.standard_b64encode(image_bytes).decode()

        result, matched = await pricer.price_recipe_image(image_b64)
        await msg.edit_text(result, parse_mode="HTML")
        if matched:
            await _send_html_report(update, matched, recipe_name="מתכון מתמונה")
    except Exception as e:
        logger.error(f"Image recipe error: {e}", exc_info=True)
        await msg.edit_text(f"❌ שגיאה: {e}")


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = pricer.db.stats()
    age   = f"{stats['last_sync_age_min']} min ago" if stats['last_sync_age_min'] is not None else "never"
    await update.message.reply_text(
        f"📊 <b>DB Status</b>\n"
        f"Products: <code>{stats['total_products']:,}</code>\n"
        f"Last file: <code>{stats['last_sync_file'] or '—'}</code>\n"
        f"Last sync: {age}",
        parse_mode="HTML"
    )


async def handle_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔄 מסנכרן מחירים...")
    try:
        result = sync_all(pricer.db)
        await msg.edit_text(
            f"✅ <b>Sync done</b>\n"
            f"Files found: {result.get('files_found', 0)}\n"
            f"Downloaded: {result.get('downloaded', 0)}\n"
            f"Rows updated: {result.get('rows_applied', 0)}\n"
            f"DB total: {pricer.db.stats()['total_products']:,} products",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Sync failed: {e}")


async def handle_browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /whiskey, /cheese, /wine etc."""
    command = update.message.text.lstrip("/").split()[0].lower()
    titles = {
        "whiskey": "🥃 וויסקי", "wine": "🍷 יינות", "beer": "🍺 בירות",
        "cheese": "🧀 גבינות", "sausage": "🌭 נקניקים ומעדנים",
        "seafood": "🐟 דגים טריים", "meat": "🥩 בשר טרי",
        "poultry": "🐔 עוף ועופות", "vodka": "🍸 וודקה",
        "spirits": "🥂 משקאות חריפים", "chocolate": "🍫 שוקולד",
        "coffee": "☕ קפה",
    }
    title = titles.get(command, f"🔍 {command}")
    msg = await update.message.reply_text(f"🔍 מחפש {title}...")
    products = browse_category(pricer.db, command)
    result = format_product_list(products, title, category=command)
    await msg.edit_text(result, parse_mode="HTML")


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /search {query}"""
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("שימוש: /search {שם מוצר}\nלדוגמה: /search גאודה")
        return
    query = parts[1].strip()
    msg = await update.message.reply_text(f"🔍 מחפש: {query}...")
    products = search_products(pricer.db, query)
    result = format_product_list(products, f"חיפוש: {query}")
    await msg.edit_text(result, parse_mode="HTML")


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /menu — show all available categories"""
    text = (
        "🏪 <b>קשת טעמים — קטלוג מוצרים</b>\n"
        "——————————————————\n"
        "בחר קטגוריה:\n\n"
        "🥩 <b>בשר ודגים</b>\n"
        "  /meat — בשר טרי\n"
        "  /poultry — עוף ועופות\n"
        "  /seafood — דגים טריים\n"
        "  /sausage — נקניקים ומעדנים\n\n"
        "🧀 <b>מוצרי חלב</b>\n"
        "  /cheese — גבינות\n\n"
        "🍷 <b>משקאות</b>\n"
        "  /wine — יינות\n"
        "  /beer — בירות\n"
        "  /whiskey — וויסקי\n"
        "  /vodka — וודקה\n"
        "  /spirits — משקאות חריפים\n\n"
        "🍫 <b>מתוקים</b>\n"
        "  /chocolate — שוקולד\n\n"
        "☕ <b>חם</b>\n"
        "  /coffee — קפה\n\n"
        "🔍 <b>חיפוש חופשי</b>\n"
        "  /search {שם} — חפש כל מוצר\n"
        "——————————————————\n"
        "📸 שלח תמונת מתכון לחישוב עלות"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def handle_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /catalog [category] — send HTML catalog file."""
    category = context.args[0] if context.args else "all"
    msg = await update.message.reply_text("📊 מכין קטלוג...")
    try:
        from catalog_html import generate_catalog_html
        html_content = generate_catalog_html(pricer.db, category)
        cat_name = category if category != "all" else "מלא"
        filename = f"קשת_טעמים_קטלוג_{cat_name}.html"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html_content)
            tmp_path = f.name
        with open(tmp_path, "rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=filename,
                caption="📊 קטלוג קשת טעמים — פתח בדפדפן",
            )
        os.unlink(tmp_path)
        await msg.delete()
    except Exception as e:
        logger.error(f"Catalog generation failed: {e}", exc_info=True)
        await msg.edit_text(f"❌ שגיאה: {e}")


async def handle_catalog_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /catalog_whiskey, /catalog_cheese etc."""
    command = update.message.text.lstrip("/").split()[0].lower()
    category = command.replace("catalog_", "")
    context.args = [category]
    await handle_catalog(update, context)


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("sync", handle_sync))
    app.add_handler(CommandHandler("menu", handle_menu))
    app.add_handler(CommandHandler("search", handle_search))
    app.add_handler(CommandHandler("catalog", handle_catalog))
    for cat in CATEGORY_KEYWORDS:
        app.add_handler(CommandHandler(cat, handle_browse))
        app.add_handler(CommandHandler(f"catalog_{cat}", handle_catalog_category))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image_recipe))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_recipe))
    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
