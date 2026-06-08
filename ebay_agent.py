import requests
import json
import os
from datetime import datetime

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

prompt = """Find 5 trending products to sell on eBay UK sourced from AliExpress China. Each must cost under 5 GBP to source and sell for 15-30 GBP on eBay. Focus on electronics and phone accessories. Return ONLY this JSON with no other text: {"products":[{"name":"product name","source_price":3.50,"sell_price":19.99,"profit":16.49,"margin":82,"ebay_title":"eBay listing title under 80 chars","ebay_description":"150 word eBay description","aliexpress_search":"search term","why_trending":"one sentence reason"}]}"""

print(f"Calling Ollama ({OLLAMA_MODEL})...")
r = requests.post(OLLAMA_URL,
    json={'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False})

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
print(f"\nFound {len(products)} products!\n")

for i, p in enumerate(products, 1):
    print(f"\nPRODUCT {i}: {p['name']}")
    print(f"Buy for: GBP{p['source_price']} | Sell for: GBP{p['sell_price']} | Profit: GBP{p['profit']}")
    print(f"eBay Title: {p['ebay_title']}")
    print(f"Why trending: {p['why_trending']}")
    print(f"AliExpress search: {p['aliexpress_search']}")

with open('ebay_listings.json','w') as f:
    json.dump(products,f,indent=2)
print("\nSaved! DONE!")