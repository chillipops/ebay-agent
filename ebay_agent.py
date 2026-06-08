"""
eBay Dropship Research Agent  v3
=================================
Pipeline:
  1. Scrape eBay UK active BIN listings for 3 categories (real live data)
  2. Fetch real median sold price from eBay UK completed listings per product
  3. Use Ollama/Mistral to estimate AliExpress source price + validate opportunity
  4. Filter: profit >= £10, shipping <= 10 days, seller rating >= 4.5, sales >= 1000/mo
  5. Save to ebay_listings.json with clickable AliExpress price-sorted buy URLs

Data quality:
  ebay_sell_price  → REAL  (live eBay UK scrape)
  ebay_sold_count  → REAL  (eBay completed listings count)
  source_price_gbp → AI ESTIMATE  (Ollama/Mistral — verify via aliexpress_url)
  aliexpress_url   → REAL price-sorted search link (click to see live prices)
"""

import sys, re, json, time, os, statistics, random
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

# ── Config ────────────────────────────────────────────────────────────────────

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral"

MIN_PROFIT_GBP      = 10.0
MAX_SHIP_DAYS       = 10
MIN_SELL_PRICE      = 15.0
MAX_SELL_PRICE      = 60.0
MAX_CANDIDATES_EACH = 10   # candidates to evaluate per category

# Tier-1 brand names → cannot be cheaply sourced from AliExpress
KNOWN_BRANDS = {
    "apple", "samsung", "sony", "bose", "jabra", "dyson", "jbl",
    "dji", "zhiyun", "feiyu", "gopro", "garmin", "fitbit",
    "anker", "belkin", "mophie", "otterbox", "spigen",
    "canon", "nikon", "motorola", "oneplus", "xiaomi", "huawei",
    "lg", "philips", "panasonic", "logitech",
}

def is_branded(title: str) -> bool:
    words = set(re.sub(r"[^a-z ]", "", title.lower()).split())
    return bool(words & KNOWN_BRANDS)

# Category searches — short keywords perform best on eBay
CATEGORIES = [
    {
        "name": "Phone Accessories",
        "searches": [
            "wireless earbuds bluetooth",
            "phone gimbal stabilizer",
            "fast wireless charger",
            "selfie ring light tripod",
        ],
    },
    {
        "name": "Car Accessories",
        "searches": [
            "car phone holder charger",
            "dash cam car camera",
            "car HUD display",
            "car LED interior lights",
        ],
    },
    {
        "name": "Home Gadgets",
        "searches": [
            "LED strip lights smart",
            "mini projector portable",
            "wifi camera indoor",
            "air purifier desktop",
        ],
    },
]

CHROME = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Upgrade-Insecure-Requests": "1",
}


# ── eBay scraper ──────────────────────────────────────────────────────────────

class EbaySession:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(CHROME)
        self._seed()

    def _seed(self):
        try:
            self.session.get("https://www.ebay.co.uk/", timeout=15)
            print("  eBay session seeded OK")
        except Exception as e:
            print(f"  eBay seed error: {e}")

    def _get(self, url: str, retries: int = 3) -> requests.Response | None:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, timeout=20)
                if "Pardon our interruption" in resp.text or len(resp.text) < 20000:
                    # Rate limited — back off and reseed
                    wait = 20 + attempt * 15
                    print(f"    [eBay] Rate limited — waiting {wait}s then reseeding...")
                    time.sleep(wait)
                    self._seed()
                    continue
                return resp
            except Exception as e:
                print(f"    [eBay] Fetch error: {e}")
                time.sleep(5)
        return None

    def search(self, keyword: str, sold: bool = False, max_items: int = 40) -> list[dict]:
        """Return [{title, price, url, listing_id}] from eBay UK."""
        params = {
            "_nkw": keyword,
            "_sop": "13" if sold else "12",
            "_ipg": str(max_items),
        }
        if not sold:
            params["LH_BIN"] = "1"
        else:
            params["LH_Complete"] = "1"
            params["LH_Sold"] = "1"

        url = "https://www.ebay.co.uk/sch/i.html?" + urlencode(params)
        resp = self._get(url)
        if resp is None:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []

        for item in soup.select(".srp-results li.s-card"):
            # Title: prefer img alt, fallback to su-title
            img = item.select_one("img[alt]")
            title = img.get("alt", "").strip() if img else ""
            if not title:
                t = item.select_one(".su-title")
                title = t.text.strip() if t else ""
            if len(title) < 6:
                continue

            # Price
            price_el = (
                item.select_one("[class*='price']") or
                item.select_one(".notranslate")
            )
            price = None
            if price_el:
                m = re.search(r"£([\d,]+\.[\d]{2})", price_el.text)
                if m:
                    price = float(m.group(1).replace(",", ""))
            if price is None or not (MIN_SELL_PRICE <= price <= MAX_SELL_PRICE):
                continue

            # URL
            link = item.select_one("a[href*='ebay.co.uk/itm']")
            url_item = link.get("href", "").split("?")[0] if link else ""

            results.append({
                "title":      title[:100],
                "price":      price,
                "url":        url_item,
                "listing_id": item.get("data-listingid", ""),
            })

        return results

    def median_sold(self, keyword: str) -> tuple[float, int]:
        items = self.search(keyword, sold=True, max_items=40)
        prices = [i["price"] for i in items]
        if not prices:
            return 0.0, 0
        return round(statistics.median(prices), 2), len(prices)


# ── Ollama ────────────────────────────────────────────────────────────────────

def ollama_json(prompt: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = requests.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=60,
            )
            text = r.json().get("response", "").strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.split("```")[0]
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as e:
            if attempt == retries - 1:
                print(f"      [Ollama error] {e}")
    return None


def analyse(title: str, sell_price: float, category: str) -> tuple[dict | None, str]:
    """Evaluate product as dropship opportunity. Returns (result, reject_reason)."""
    prompt = f"""You are an expert eBay dropshipping analyst sourcing generic products from AliExpress China to sell in the UK.

Product listing: "{title}"
Category: {category}
eBay UK sell price: £{sell_price:.2f}

Assess this as a dropship opportunity. Return ONLY a JSON object with no other text:

{{
  "viable": true,
  "source_price_gbp": 4.50,
  "shipping_days_to_uk": 7,
  "seller_rating": 4.7,
  "monthly_sales_aliexpress": 3000,
  "aliexpress_search_term": "short 3-5 word search term for cheapest generic supplier",
  "reject_reason": ""
}}

Set viable=true ONLY if ALL of these are true:
1. Product is generic/unbranded (can be sourced from Chinese supplier on AliExpress)
2. sell_price - source_price_gbp >= {MIN_PROFIT_GBP} (profit at least £{MIN_PROFIT_GBP})
3. shipping_days_to_uk <= {MAX_SHIP_DAYS} (AliExpress Choice ships UK in 7-10 days)
4. seller_rating >= 4.5
5. monthly_sales_aliexpress >= 1000

source_price_gbp: realistic AliExpress wholesale price in GBP (small electronics £1-8, gadgets £3-12)
aliexpress_search_term: the 3-5 generic words a buyer would type to find the cheapest supplier

If not viable, explain briefly in reject_reason."""

    data = ollama_json(prompt)
    if not data:
        return None, "Ollama returned no data"
    if not data.get("viable"):
        return None, data.get("reject_reason", "Not viable")

    src   = float(data.get("source_price_gbp", 0))
    days  = int(data.get("shipping_days_to_uk", 99))
    rat   = float(data.get("seller_rating", 0))
    sales = int(data.get("monthly_sales_aliexpress", 0))
    profit = round(sell_price - src, 2)
    margin = round((profit / sell_price) * 100) if sell_price else 0

    # Hard-enforce filters
    if profit < MIN_PROFIT_GBP:
        return None, f"Profit £{profit:.2f} < £{MIN_PROFIT_GBP}"
    if days > MAX_SHIP_DAYS:
        return None, f"Ships in {days}d > max {MAX_SHIP_DAYS}d"
    if rat < 4.5:
        return None, f"Rating {rat} < 4.5"
    if sales < 1000:
        return None, f"Sales {sales}/mo < 1000"

    return {
        "source_price_gbp":      src,
        "profit_gbp":            profit,
        "margin_pct":            margin,
        "shipping_days":         days,
        "seller_rating":         rat,
        "monthly_sales":         sales,
        "aliexpress_search_term": data.get("aliexpress_search_term", ""),
    }, ""


def aliexpress_url(term: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
    return f"https://www.aliexpress.com/w/wholesale-{slug}.html?SortType=price_asc"


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print("=" * 65)
    print("  eBay Dropship Research Agent  v3")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')} | Ollama: {OLLAMA_MODEL}")
    print("=" * 65)

    ebay = EbaySession()
    all_opps: list[dict] = []
    total_rejected = 0

    for cat in CATEGORIES:
        print(f"\n{'─'*65}\nCATEGORY: {cat['name']}\n{'─'*65}")
        all_active: list[dict] = []

        for keyword in cat["searches"]:
            print(f"  eBay search: '{keyword}'...")
            listings = ebay.search(keyword, sold=False, max_items=30)
            # Filter branded
            before = len(listings)
            listings = [l for l in listings if not is_branded(l["title"])]
            print(f"    {len(listings)} unbranded listings (£{MIN_SELL_PRICE}-£{MAX_SELL_PRICE})"
                  f"  [{before - len(listings)} branded filtered]")
            all_active.extend(listings)
            time.sleep(random.uniform(1.5, 2.5))  # polite delay

        # Deduplicate
        seen, unique = set(), []
        for item in sorted(all_active, key=lambda x: x["price"], reverse=True):
            key = " ".join(item["title"].lower().split()[:4])
            if key not in seen:
                seen.add(key)
                unique.append(item)

        print(f"\n  Unique candidates: {len(unique)}")
        if not unique:
            print("  No candidates — skipping")
            continue

        cat_opps = 0
        for item in unique[:MAX_CANDIDATES_EACH]:
            title = item["title"]
            active_price = item["price"]

            # Real sold price for this product
            kw4 = " ".join(title.split()[:4])
            print(f"\n  > [{active_price:.0f}] {title[:62]}")
            med_sold, sold_count = ebay.median_sold(kw4)
            time.sleep(random.uniform(1.0, 2.0))

            sell_price = med_sold if med_sold >= MIN_SELL_PRICE else active_price
            print(f"    Active £{active_price:.2f} | Sold median £{med_sold:.2f}"
                  f" ({sold_count} records) | Using £{sell_price:.2f}")

            result, reason = analyse(title, sell_price, cat["name"])
            time.sleep(0.5)

            if result is None:
                print(f"    SKIP: {reason}")
                total_rejected += 1
                continue

            search_term = result["aliexpress_search_term"] or kw4
            opp = {
                "rank":              len(all_opps) + 1,
                "product_name":      title,
                "category":          cat["name"],
                "ebay_sell_price":   sell_price,
                "ebay_active_price": active_price,
                "ebay_sold_count":   sold_count,
                "ebay_url":          item["url"],
                "source_price_gbp":  result["source_price_gbp"],
                "profit_gbp":        result["profit_gbp"],
                "margin_pct":        result["margin_pct"],
                "shipping_days":     result["shipping_days"],
                "seller_rating":     result["seller_rating"],
                "monthly_sales_est": result["monthly_sales"],
                "aliexpress_search": search_term,
                "aliexpress_url":    aliexpress_url(search_term),
                "data_quality": {
                    "ebay_sell_price": "REAL — live eBay UK scrape",
                    "ebay_sold_count": "REAL — eBay UK completed listings",
                    "source_price":    "AI ESTIMATE — verify via aliexpress_url",
                    "aliexpress_url":  "REAL — price-sorted direct link",
                },
            }
            all_opps.append(opp)
            cat_opps += 1

            print(f"    PASS | Profit £{result['profit_gbp']:.2f} ({result['margin_pct']}%) | "
                  f"Ships {result['shipping_days']}d | "
                  f"Rating {result['seller_rating']} | "
                  f"Sales {result['monthly_sales']:,}/mo")

        print(f"\n  {cat['name']}: {cat_opps} opportunities found")

    # ── Final summary ─────────────────────────────────────────────────────────

    all_opps.sort(key=lambda x: x["profit_gbp"], reverse=True)
    for i, o in enumerate(all_opps, 1):
        o["rank"] = i

    print(f"\n{'='*65}")
    print(f"RESULTS: {len(all_opps)} opportunities  |  {total_rejected} rejected")
    print(f"{'='*65}")

    for o in all_opps:
        print(f"\n#{o['rank']}  [{o['category']}]  Profit: £{o['profit_gbp']:.2f} ({o['margin_pct']}%)")
        print(f"     {o['product_name'][:72]}")
        print(f"     eBay: £{o['ebay_sell_price']:.2f} sell  |  Source est: £{o['source_price_gbp']:.2f}  |  Ships {o['shipping_days']}d  |  Rating {o['seller_rating']}")
        print(f"     eBay URL:       {o['ebay_url'][:70]}")
        print(f"     AliExpress URL: {o['aliexpress_url'][:70]}")

    out = {
        "run_at":       datetime.now().isoformat(),
        "filters":      {"min_profit_gbp": MIN_PROFIT_GBP, "max_ship_days": MAX_SHIP_DAYS,
                         "min_sell_price": MIN_SELL_PRICE, "min_seller_rating": 4.5,
                         "min_monthly_sales": 1000},
        "data_note":    ("eBay prices = REAL live data. "
                         "AliExpress source prices = AI estimates — verify by clicking aliexpress_url."),
        "total":        len(all_opps),
        "rejected":     total_rejected,
        "opportunities": all_opps,
    }

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ebay_listings.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\nSaved to ebay_listings.json | {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    run()
