"""
Test the recipe pricer locally without Telegram.
Usage: python test_local.py <price_file.gz> [price_file2.gz ...]
"""

import asyncio
import os
import sys
from matcher import RecipePricer

SAMPLE_RECIPE_HE = """
מתכון: שניצל עגל עם גבינה

מרכיבים:
- 500 גרם חזה עגל פרוס דק
- 200 גרם גבינה בולגרית
- 2 ביצים
- קמח לציפוי
- מלח ופלפל
- שמן לטיגון
"""

SAMPLE_RECIPE_EN = """
Veal Schnitzel with cheese

Ingredients:
- 500g veal breast, thinly sliced
- 200g Bulgarian cheese (goat)
- 2 eggs
- flour for coating
- salt and pepper
- oil for frying
"""


async def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Set ANTHROPIC_API_KEY env var")
        sys.exit(1)

    price_files = sys.argv[1:] if len(sys.argv) > 1 else []
    if not price_files:
        print("Usage: python test_local.py <price_file.gz> ...")
        sys.exit(1)

    pricer = RecipePricer(api_key)
    pricer.load_price_files(price_files)

    print("=== Testing Hebrew recipe ===")
    result = await pricer.price_recipe(SAMPLE_RECIPE_HE)
    print(result)

    print("\n=== Testing English recipe ===")
    result = await pricer.price_recipe(SAMPLE_RECIPE_EN)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
