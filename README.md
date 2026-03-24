# 🛒 Recipe Price Bot

Telegram bot that parses a recipe, finds ingredients in Israeli supermarket price XMLs, and estimates the total dish cost.

## Setup

```bash
pip install -r requirements.txt

export TELEGRAM_TOKEN=your_bot_token
export ANTHROPIC_API_KEY=your_key
```

## How to get price files

### Option A — Manual (for testing)
Download a price file from the Consumer Authority links page:
https://www.gov.il/he/departments/legalInfo/cpfta_prices_regulations

Files follow the pattern:
- `PriceFull{ChainId}-{StoreId}-{YYYYMMDDhhmm}.gz` → full catalog
- `Price{ChainId}-{StoreId}-{YYYYMMDDhhmm}.gz` → delta (price changes only)

**Always use PriceFull for the product DB.**

### Option B — Auto-fetch (add to fetcher.py)
Each chain publishes at a base URL. For Keshet Teamim:
```
http://keshet-teamim.co.il/prices/PriceFull7290785400000-{StoreId}-latest.gz
```

## Run locally (no Telegram)

```bash
python test_local.py /path/to/PriceFull7290785400000-008-*.gz
```

## Run the Telegram bot

```bash
# Load a specific price file on startup — edit bot.py to call:
# pricer.load_price_files(["PriceFull7290785400000-008-....gz"])

python bot.py
```

## How it works

1. **Claude extracts ingredients** from the recipe text (Hebrew or English)
2. **rapidfuzz pre-filters** the product list (~1000s of products → top 15 candidates per ingredient)
3. **Claude re-ranks candidates** and selects the best match + alternatives
4. **Cost is estimated** by normalizing recipe quantities to product unit prices

## Extending to more chains

Add entries to `CHAIN_CONFIGS` in `fetcher.py`:
```python
"rami_levy": {
    "chain_id": "7290058140886",
    "base_url": "http://..."
    "stores": ["001", ...],
    "name": "רמי לוי",
}
```

Then pass multiple price files to `load_price_files()`.
