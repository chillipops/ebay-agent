import requests
import json
import os
import time
from urllib.parse import quote_plus

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip()
    print("Loaded .env OK")

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

OLLAMA_URL = 'http://localhost:11434/api/generate'
OLLAMA_MODEL = 'mistral'


def aliexpress_search_url(search_term):
    """Return a direct AliExpress search URL sorted by lowest price."""
    return (
        "https://www.aliexpress.com/w/wholesale-"
        + quote_plus(search_term).replace('+', '-')
        + ".html?SortType=price_asc"
    )


def verify_price_with_ollama(product_name, search_term):
    """
    Ask Ollama for a realistic current AliExpress price estimate for the product.
    Returns a float price in GBP, or None on failure.
    """
    prompt = (
        f"What is a realistic current AliExpress wholesale price in GBP for: "
        f'"{product_name}" (search term: "{search_term}")? '
        f"Reply with ONLY a JSON object like: "
        f'{{\"price_gbp\": 2.50, \"note\": \"typical cheap Chinese supplier price\"}}'
    )
    try:
        r = requests.post(
            OLLAMA_URL,
            json={'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False},
            timeout=30,
        )
        text = r.json().get('response', '').strip()
        if '```' in text:
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.split('```')[0].strip()
        data = json.loads(text)
        price = float(data.get('price_gbp', 0))
        if 0.10 < price < 50:
            return price
    except Exception:
        pass
    return None


# ── Ollama: generate product ideas ──────────────────────────────────────────

prompt = (
    "Find 5 trending products to sell on eBay UK sourced from AliExpress China. "
    "Each must cost under 5 GBP to source and sell for 15-30 GBP on eBay. "
    "Focus on electronics and phone accessories. "
    "Return ONLY this JSON with no other text: "
    '{"products":[{"name":"product name","source_price":3.50,"sell_price":19.99,'
    '"profit":16.49,"margin":82,"ebay_title":"eBay listing title under 80 chars",'
    '"ebay_description":"150 word eBay description","aliexpress_search":"search term",'
    '"why_trending":"one sentence reason"}]}'
)

print(f"Calling Ollama ({OLLAMA_MODEL}) for product ideas...")
r = requests.post(OLLAMA_URL, json={'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False})

print(f"Status: {r.status_code}")
data = r.json()

if r.status_code != 200:
    print(f"Error: {data}")
    exit()

text = data['response'].strip()
if '```' in text:
    text = text.split('```')[1]
    if text.startswith('json'):
        text = text[4:]
    text = text.split('```')[0].strip()

products = json.loads(text)['products']
print(f"\nFound {len(products)} products.")
print("Refining source prices and building AliExpress URLs...\n")

# ── Price refinement + AliExpress URL ───────────────────────────────────────

for i, p in enumerate(products, 1):
    search_term = p.get('aliexpress_search', p['name'])
    print(f"  [{i}/{len(products)}] {p['name']}")

    # Build a direct price-sorted AliExpress search URL
    ali_url = aliexpress_search_url(search_term)
    p['aliexpress_url'] = ali_url

    # Ask Ollama to sanity-check/refine the source price
    refined = verify_price_with_ollama(p['name'], search_term)
    if refined and abs(refined - p['source_price']) > 0.50:
        old = p['source_price']
        p['source_price'] = refined
        real_profit = round(p['sell_price'] - refined, 2)
        real_margin = round((real_profit / p['sell_price']) * 100)
        p['profit'] = real_profit
        p['margin'] = real_margin
        print(f"         Price refined: GBP{old} -> GBP{refined}")
    else:
        print(f"         Price unchanged: GBP{p['source_price']}")

    p['price_source'] = 'ai_estimate'
    print(f"         AliExpress URL: {ali_url}")
    time.sleep(0.5)

# ── Print summary ────────────────────────────────────────────────────────────

print("\n" + "=" * 62)
print("SUMMARY")
print("=" * 62)

for i, p in enumerate(products, 1):
    print(f"\nPRODUCT {i}: {p['name']}")
    print(f"  Source: GBP{p['source_price']} (AI est) | Sell: GBP{p['sell_price']} | Profit: GBP{p['profit']} ({p['margin']}%)")
    print(f"  eBay Title: {p['ebay_title']}")
    print(f"  Why trending: {p.get('why_trending', 'N/A')}")
    print(f"  Buy URL: {p['aliexpress_url']}")

with open('ebay_listings.json', 'w') as f:
    json.dump(products, f, indent=2)
print("\nSaved to ebay_listings.json!")
print("\nNOTE: source_price is an AI estimate. Click 'Buy URL' to see live AliExpress prices sorted cheapest first.")
