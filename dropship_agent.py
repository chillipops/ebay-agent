"""
Autonomous eBay Dropship Agent v3.0 - COMPETITION + MULTI-SOURCE
=================================================================
Save to: C:\\Users\\Owner\\ebay_agent\\dropship_agent.py

New in v3.0:
  - Real competitor count, avg price, saturation tracking
  - Google Shopping scraping
  - PriceRunner UK scraping  
  - WatchCount (eBay watch counts = demand signal)
  - Price trend detection
  - Market saturation alerts
  - Competition growth tracking
"""

import json, time, os, re, random, asyncio, requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────

# "sandbox" (safe default, no live data) or "production" (real eBay data)
EBAY_ENV        = os.getenv("EBAY_ENV", "sandbox").strip().lower()
EBAY_DEV_ID     = os.getenv("EBAY_DEV_ID", "")
EBAY_APP_ID     = os.getenv(f"EBAY_APP_ID_{EBAY_ENV.upper()}", "")
EBAY_CERT_ID    = os.getenv(f"EBAY_CERT_ID_{EBAY_ENV.upper()}", "")
EBAY_FINDING_URL = (
    "https://svcs.sandbox.ebay.com/services/search/FindingService/v1"
    if EBAY_ENV == "sandbox" else
    "https://svcs.ebay.com/services/search/FindingService/v1"
)

SCRAPER_API_KEY    = ""
OLLAMA_URL         = "http://localhost:11434/api/generate"
OLLAMA_MODEL       = "mistral"
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID   = "629496421"

SCAN_INTERVAL_MIN  = 60
REVIEW_EVERY_N     = 4
MIN_PROFIT         = 8.00
MAX_BUY_PRICE      = 15.00
MAX_COMPETITION    = "Medium"
MEMORY_FILE        = "dropship_memory.json"
LISTINGS_FILE      = "ebay_listings.txt"
USD_TO_GBP         = 0.79

BLACKLIST_KEYWORDS = [
    "iphone case", "airpods case", "screen protector",
    "fidget", "generic cable", "phone case"
]

SEARCH_TERMS = [
    "car phone holder", "led strip lights", "cable organiser box",
    "magnetic wireless charger", "laptop cooling stand", "kitchen gadget uk",
    "pet grooming glove", "portable usb hub", "resistance band set",
    "garden kneeler seat", "desk cable management", "baby food maker",
    "bluetooth key finder", "monitor riser stand", "under desk drawer",
    "shoe cleaning kit", "electric lint remover", "fridge organiser bins",
    "weighted blanket", "shower caddy", "bath mat non slip",
    "garden tool set", "bluetooth tracker", "screen cleaner kit",
]

# ─── LOGGING ─────────────────────────────────────────────────────────────────

def log(msg, lvl=""):
    icons = {"GOOD": "✓", "WARN": "!", "AGENT": "🤖", "DATA": "📦", "SKIP": "✗", "COMP": "⚔", "": "•"}
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {icons.get(lvl,'•')} {msg}")

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except: pass

def gbp(usd): return round(float(usd) * USD_TO_GBP, 2)

def ask_ollama(prompt, temp=0.3):
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL, "prompt": prompt,
            "stream": False, "options": {"temperature": temp}
        }, timeout=90)
        return r.json().get("response", "").strip()
    except Exception as e:
        log(f"Ollama error: {e}", "WARN")
        return ""

def extract_json(text, kind="array"):
    text = re.sub(r"```[a-z]*", "", text).strip()
    s, e = (text.find("["), text.rfind("]")) if kind == "array" else (text.find("{"), text.rfind("}"))
    if s == -1 or e == -1: return None
    try: return json.loads(text[s:e+1])
    except: return None

def is_blacklisted(title):
    return any(kw in title.lower() for kw in BLACKLIST_KEYWORDS)

def get_season():
    m = datetime.now().month
    if m in [12,1,2]: return "winter"
    if m in [3,4,5]:  return "spring"
    if m in [6,7,8]:  return "summer"
    return "autumn"

# ─── MEMORY ──────────────────────────────────────────────────────────────────

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "cycle_count": 0, "search_scores": {}, "all_products": [],
        "strategy_notes": [], "last_review": None,
        "total_found": 0, "best_ever": None,
        "search_terms": SEARCH_TERMS.copy(),
        "data_source_stats": {"ebay_api": 0, "playwright": 0, "google": 0, "pricerunner": 0, "watchcount": 0, "ollama_fallback": 0},
        "competition_history": {},  # keyword -> [{date, count, avg_price}]
        "saturated_keywords": [],
    }

def save_memory(mem):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2, ensure_ascii=False)

# ─── DATA SOURCE 1: eBay API ──────────────────────────────────────────────────

def ebay_api_search(keyword, max_results=25):
    if not EBAY_APP_ID: return []
    try:
        params = {
            "OPERATION-NAME": "findCompletedItems",
            "SERVICE-VERSION": "1.0.0",
            "SECURITY-APPNAME": EBAY_APP_ID,
            "RESPONSE-DATA-FORMAT": "JSON",
            "keywords": keyword,
            "itemFilter(0).name": "SoldItemsOnly",
            "itemFilter(0).value": "true",
            "itemFilter(1).name": "Currency",
            "itemFilter(1).value": "GBP",
            "itemFilter(2).name": "MinPrice",
            "itemFilter(2).value": "8",
            "itemFilter(3).name": "MaxPrice",
            "itemFilter(3).value": "60",
            "sortOrder": "BestMatch",
            "paginationInput.entriesPerPage": max_results,
        }
        r = requests.get(EBAY_FINDING_URL, params=params, timeout=15)
        items = (r.json().get("findCompletedItemsResponse",[{}])[0]
                        .get("searchResult",[{}])[0]
                        .get("item",[]))
        results = []
        for item in items:
            try:
                title = item["title"][0]
                if is_blacklisted(title): continue
                price = float(item["sellingStatus"][0]["currentPrice"][0]["__value__"])
                results.append({"title": title, "ebay_sell_price": round(price,2), "url": item["viewItemURL"][0], "source": "ebay_api"})
            except: continue
        log(f"eBay API: {len(results)} items for '{keyword}'", "DATA")
        return results
    except Exception as e:
        log(f"eBay API error: {e}", "WARN")
        return []

# ─── DATA SOURCE 2: Playwright eBay ──────────────────────────────────────────

async def playwright_scrape_ebay(keyword, max_items=20):
    """Session-based eBay scraper - replaces broken Playwright version."""
    import statistics
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    }
    try:
        session = requests.Session()
        session.headers.update(headers)
        session.get("https://www.ebay.co.uk/", timeout=15)
        url = (
            f"https://www.ebay.co.uk/sch/i.html"
            f"?_nkw={keyword.replace(' ', '+')}"
            f"&LH_Sold=1&LH_Complete=1&_sop=12&_ipg=48"
        )
        resp = session.get(url, timeout=20)
        if resp.status_code != 200 or len(resp.text) < 10000:
            log(f"Playwright eBay: blocked for '{keyword}'", "WARN")
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".s-item")[:max_items]:
            try:
                title_el = item.select_one(".s-item__title")
                price_el = item.select_one(".s-item__price")
                link_el  = item.select_one(".s-item__link")
                if not title_el or not price_el:
                    continue
                title = title_el.text.strip()
                if title in ("Shop on eBay", "") or is_blacklisted(title):
                    continue
                price_match = re.search(r"[\d.]+", price_el.text.replace("£", "").replace(",", ""))
                if not price_match:
                    continue
                price = float(price_match.group())
                if 8 <= price <= 60:
                    results.append({
                        "title": title[:100],
                        "ebay_sell_price": round(price, 2),
                        "url": link_el["href"] if link_el else "",
                        "source": "requests_ebay"
                    })
            except:
                continue
        log(f"Playwright eBay: {len(results)} items for '{keyword}'", "DATA")
    except Exception as e:
        log(f"Playwright eBay error: {e}", "WARN")
    return results

# ─── DATA SOURCE 3: Google Shopping ──────────────────────────────────────────

async def scrape_google_shopping(keyword):
    """Scrape Google Shopping for UK prices."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []
    results = []
    url = f"https://www.google.co.uk/search?q={keyword.replace(' ','+')}&tbm=shop&hl=en-GB&gl=gb"
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="en-GB"
            )
            page = await ctx.new_page()
            await page.route("**/*.{png,jpg,jpeg,gif,svg,woff}", lambda r: r.abort())
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(2000)
            html = await page.content()
            await browser.close()

        soup = BeautifulSoup(html, "html.parser")
        # Google Shopping price patterns
        price_els = soup.find_all(string=re.compile(r"£[\d,.]+"))
        seen = set()
        for el in price_els[:15]:
            match = re.search(r"£([\d,.]+)", str(el))
            if match:
                try:
                    price = float(match.group(1).replace(",",""))
                    if 5 <= price <= 60 and price not in seen:
                        seen.add(price)
                        results.append({"price_gbp": price, "source": "google_shopping"})
                except: continue

        log(f"Google Shopping: {len(results)} prices for '{keyword}'", "DATA")
    except Exception as e:
        log(f"Google Shopping error: {e}", "WARN")
    return results

# ─── DATA SOURCE 4: PriceRunner UK ───────────────────────────────────────────

async def scrape_pricerunner(keyword):
    """Scrape PriceRunner for UK market prices."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []
    results = []
    url = f"https://www.pricerunner.co.uk/search?q={keyword.replace(' ','+')}"
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="en-GB"
            )
            page = await ctx.new_page()
            await page.route("**/*.{png,jpg,jpeg,gif,svg,woff}", lambda r: r.abort())
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(2000)
            html = await page.content()
            await browser.close()

        soup = BeautifulSoup(html, "html.parser")
        price_els = soup.find_all(string=re.compile(r"£[\d,.]+"))
        seen = set()
        for el in price_els[:15]:
            match = re.search(r"£([\d,.]+)", str(el))
            if match:
                try:
                    price = float(match.group(1).replace(",",""))
                    if 5 <= price <= 60 and price not in seen:
                        seen.add(price)
                        results.append({"price_gbp": price, "source": "pricerunner"})
                except: continue

        log(f"PriceRunner: {len(results)} prices for '{keyword}'", "DATA")
    except Exception as e:
        log(f"PriceRunner error: {e}", "WARN")
    return results

# ─── DATA SOURCE 5: WatchCount (eBay demand signal) ──────────────────────────

async def scrape_watchcount(keyword):
    """
    WatchCount shows how many people are watching eBay listings.
    High watch count = high demand. This is a powerful demand signal.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"watch_count": 0, "top_price": 0}
    
    result = {"watch_count": 0, "top_price": 0, "top_title": ""}
    url = f"http://www.watchcount.com/main.php?mode=sold&kwd={keyword.replace(' ','+')}&cid=0&sort=1"
    
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="en-GB"
            )
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(2000)
            html = await page.content()
            await browser.close()

        soup = BeautifulSoup(html, "html.parser")
        
        # Get total watch count
        watch_els = soup.find_all(string=re.compile(r"\d+\s*watch"))
        total_watches = 0
        for el in watch_els[:10]:
            match = re.search(r"(\d+)", str(el))
            if match:
                total_watches += int(match.group(1))
        
        result["watch_count"] = total_watches
        
        # Get top price
        price_els = soup.find_all(string=re.compile(r"£[\d,.]+"))
        prices = []
        for el in price_els[:5]:
            match = re.search(r"£([\d,.]+)", str(el))
            if match:
                try: prices.append(float(match.group(1).replace(",","")))
                except: pass
        if prices:
            result["top_price"] = max(prices)

        log(f"WatchCount: {total_watches} watches for '{keyword}'", "DATA")
    except Exception as e:
        log(f"WatchCount error: {e}", "WARN")
    
    return result

# ─── COMPETITION TRACKER ─────────────────────────────────────────────────────

async def get_competition_data(keyword, memory):
    """
    Full competition analysis:
    - How many sellers
    - Average price
    - Price range
    - Is market growing or shrinking
    - Saturation alert
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {}

    comp_data = {
        "seller_count": 0,
        "avg_price": 0,
        "min_price": 0,
        "max_price": 0,
        "price_range": 0,
        "saturation_level": "unknown",
        "trend": "stable",
        "prices": []
    }

    url = f"https://www.ebay.co.uk/sch/i.html?_nkw={keyword.replace(' ','+')}&LH_BIN=1&_sop=12"
    
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="en-GB", viewport={"width": 1366, "height": 768}
            )
            page = await ctx.new_page()
            await page.route("**/*.{png,jpg,jpeg,gif,svg,woff}", lambda r: r.abort())
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(2000)
            html = await page.content()
            await browser.close()

        soup = BeautifulSoup(html, "html.parser")

        # Get seller count from results header
        count_el = soup.select_one(".srp-controls__count-heading, .listingscnt")
        if count_el:
            match = re.search(r"([\d,]+)", count_el.text.replace(",",""))
            if match:
                comp_data["seller_count"] = int(match.group(1))

        # Get all prices
        prices = []
        for item in soup.select(".s-item")[:30]:
            price_el = item.select_one(".s-item__price")
            if price_el:
                match = re.search(r"[\d.]+", price_el.text.replace("£","").replace(",",""))
                if match:
                    try:
                        price = float(match.group())
                        if 5 <= price <= 100:
                            prices.append(price)
                    except: pass

        if prices:
            comp_data["prices"]     = prices[:10]
            comp_data["avg_price"]  = round(sum(prices)/len(prices), 2)
            comp_data["min_price"]  = round(min(prices), 2)
            comp_data["max_price"]  = round(max(prices), 2)
            comp_data["price_range"]= round(max(prices) - min(prices), 2)

        # Saturation levels
        count = comp_data["seller_count"]
        if count == 0:      comp_data["saturation_level"] = "unknown"
        elif count < 50:    comp_data["saturation_level"] = "low"
        elif count < 200:   comp_data["saturation_level"] = "medium"
        elif count < 500:   comp_data["saturation_level"] = "high"
        else:               comp_data["saturation_level"] = "saturated"

        # Compare to last time we checked (trend detection)
        history = memory.get("competition_history", {}).get(keyword, [])
        if history:
            last_count = history[-1].get("count", 0)
            if count > last_count * 1.2:
                comp_data["trend"] = "growing_fast"
            elif count > last_count * 1.05:
                comp_data["trend"] = "growing"
            elif count < last_count * 0.95:
                comp_data["trend"] = "shrinking"
            else:
                comp_data["trend"] = "stable"

        # Save to history
        if "competition_history" not in memory:
            memory["competition_history"] = {}
        if keyword not in memory["competition_history"]:
            memory["competition_history"][keyword] = []
        memory["competition_history"][keyword].append({
            "date": datetime.now().isoformat(),
            "count": count,
            "avg_price": comp_data["avg_price"]
        })
        # Keep only last 10 readings
        memory["competition_history"][keyword] = memory["competition_history"][keyword][-10:]

        # Flag as saturated
        if comp_data["saturation_level"] == "saturated":
            if keyword not in memory.get("saturated_keywords", []):
                memory.setdefault("saturated_keywords", []).append(keyword)
                log(f"SATURATED MARKET: {keyword} ({count} sellers)", "WARN")

        log(f"Competition: {count} sellers, avg £{comp_data['avg_price']}, {comp_data['saturation_level']} saturation, trend: {comp_data['trend']}", "COMP")

    except Exception as e:
        log(f"Competition check error: {e}", "WARN")

    return comp_data

# ─── AliExpress Prices ────────────────────────────────────────────────────────

async def playwright_scrape_aliexpress(search_term, max_items=12):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []
    results = []
    url = f"https://www.aliexpress.com/wholesale?SearchText={search_term.replace(' ','+')}&SortType=total_tranpro_desc"
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="en-GB"
            )
            page = await ctx.new_page()
            await page.route("**/*.{png,jpg,jpeg,gif,svg,woff}", lambda r: r.abort())
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3500)
            html = await page.content()
            await browser.close()
        soup = BeautifulSoup(html, "html.parser")
        price_els = soup.find_all(string=re.compile(r"US \$[\d.]+"))
        seen = set()
        for el in price_els[:max_items]:
            match = re.search(r"US \$([\d.]+)", str(el))
            if match:
                price_usd = float(match.group(1))
                if 0.50 < price_usd < 20 and price_usd not in seen:
                    seen.add(price_usd)
                    results.append({"price_usd": price_usd, "price_gbp": gbp(price_usd), "source": "aliexpress"})
        if not results:
            all_text = soup.get_text()
            for m in re.findall(r"\$([\d.]+)", all_text)[:max_items]:
                try:
                    p = float(m)
                    if 0.50 < p < 20 and p not in seen:
                        seen.add(p)
                        results.append({"price_usd": p, "price_gbp": gbp(p), "source": "aliexpress_text"})
                except: pass
        log(f"AliExpress: {len(results)} prices for '{search_term}'", "DATA")
    except Exception as e:
        log(f"AliExpress error: {e}", "WARN")
    return results

# ─── AI ANALYSIS ─────────────────────────────────────────────────────────────

def analyse_product(ebay_item, ali_prices, comp_data, watch_data, google_prices, pricerunner_prices):
    sell_price = ebay_item.get("ebay_sell_price", 0)
    title      = ebay_item.get("title", "")

    # Best buy price from all sources
    all_buy_prices = [p["price_gbp"] for p in ali_prices]
    if google_prices:
        all_buy_prices += [p["price_gbp"] for p in google_prices if p["price_gbp"] < sell_price * 0.4]
    
    if all_buy_prices:
        buy_price = sorted(all_buy_prices)[len(all_buy_prices)//2]
    else:
        buy_price = round(sell_price * 0.25, 2)

    ebay_fees = round(sell_price * 0.125, 2)
    postage   = 3.50
    profit    = round(sell_price - buy_price - ebay_fees - postage, 2)

    if profit < MIN_PROFIT or buy_price > MAX_BUY_PRICE:
        return None

    # Skip saturated markets
    if comp_data.get("saturation_level") == "saturated":
        log(f"Skipping saturated: {title[:40]}", "SKIP")
        return None

    # Market data summary for AI
    comp_summary = f"{comp_data.get('seller_count','?')} sellers, avg £{comp_data.get('avg_price','?')}, {comp_data.get('saturation_level','?')} saturation, trend: {comp_data.get('trend','?')}"
    watch_summary = f"{watch_data.get('watch_count',0)} watches on eBay"
    price_sources = len(ali_prices) + len(google_prices) + len(pricerunner_prices)

    prompt = f"""UK eBay dropshipping expert. Analyse this opportunity:

Product: {title}
eBay sold price: £{sell_price}
Buy price: £{buy_price} (from {price_sources} sources)
Net profit: £{profit}
Competition: {comp_summary}
Demand signal: {watch_summary}
Season: {get_season()}

Reply ONLY as JSON:
{{
  "ebay_title": "SEO title under 80 chars",
  "competition_level": "Low|Medium|High",
  "demand_score": <1-10>,
  "seasonal_relevance": "year-round|{get_season()}-only|avoid",
  "unique_angle": "one sentence edge",
  "ebay_description": "3-4 sentence UK listing description",
  "key_selling_points": ["point1","point2","point3"],
  "aliexpress_search": "best search term",
  "risk_flag": "concern or null",
  "recommended_sell_price": <number>,
  "undercut_competitors_by": <number>
}}"""

    response = ask_ollama(prompt)
    ai = extract_json(response, "object") or {
        "ebay_title": title[:80], "competition_level": "Medium",
        "demand_score": 5, "seasonal_relevance": "year-round",
        "unique_angle": "Proven UK seller",
        "ebay_description": f"Quality {title}. Fast UK dispatch.",
        "key_selling_points": ["Quality","Fast dispatch","UK seller"],
        "aliexpress_search": title, "risk_flag": None,
        "recommended_sell_price": sell_price,
        "undercut_competitors_by": 0
    }

    if MAX_COMPETITION == "Medium" and ai.get("competition_level") == "High":
        log(f"Skipping high competition: {title[:40]}", "SKIP")
        return None

    search_term = ai.get("aliexpress_search", title)
    return {
        "title":                  title,
        "ebay_title":             ai.get("ebay_title", title[:80]),
        "price_gbp":              buy_price,
        "ebay_sell_price":        sell_price,
        "recommended_sell_price": ai.get("recommended_sell_price", sell_price),
        "undercut_by":            ai.get("undercut_competitors_by", 0),
        "ebay_fees":              ebay_fees,
        "postage_cost":           postage,
        "profit_gbp":             profit,
        "competition_level":      ai.get("competition_level","Medium"),
        "competitor_count":       comp_data.get("seller_count", -1),
        "avg_competitor_price":   comp_data.get("avg_price", 0),
        "competition_trend":      comp_data.get("trend","stable"),
        "saturation_level":       comp_data.get("saturation_level","unknown"),
        "price_range":            comp_data.get("price_range", 0),
        "watch_count":            watch_data.get("watch_count", 0),
        "demand_score":           ai.get("demand_score", 5),
        "seasonal_relevance":     ai.get("seasonal_relevance","year-round"),
        "unique_angle":           ai.get("unique_angle",""),
        "ebay_description":       ai.get("ebay_description",""),
        "key_selling_points":     ai.get("key_selling_points",[]),
        "risk_flag":              ai.get("risk_flag"),
        "aliexpress_search":      search_term,
        "aliexpress_url":         f"https://www.aliexpress.com/wholesale?SearchText={search_term.replace(' ','+')}",
        "ebay_url":               ebay_item.get("url",""),
        "data_sources":           price_sources,
        "real_data":              ebay_item.get("source","") != "ai_estimate",
        "found_at":               datetime.now().isoformat(),
        "season":                 get_season(),
    }

# ─── SCAN PIPELINE ───────────────────────────────────────────────────────────

async def scan_keyword_async(keyword, memory):
    log(f"Scanning: '{keyword}'")
    products = []

    # Skip saturated keywords
    if keyword in memory.get("saturated_keywords", []):
        log(f"Skipping saturated keyword: {keyword}", "SKIP")
        return products, memory

    # Get eBay sold data
    ebay_items = []
    if EBAY_APP_ID:
        ebay_items = ebay_api_search(keyword)
        if ebay_items: memory["data_source_stats"]["ebay_api"] += 1

    if not ebay_items:
        ebay_items = await playwright_scrape_ebay(keyword)
        if ebay_items: memory["data_source_stats"]["playwright"] += 1

    if not ebay_items:
        log(f"No real data for '{keyword}' — AI estimates", "WARN")
        memory["data_source_stats"]["ollama_fallback"] += 1
        ebay_items = generate_fallback_products(keyword)

    if not ebay_items:
        return products, memory

    # Run all data sources in parallel
    search = " ".join(ebay_items[0]["title"].split()[:4])
    
    ali_task       = playwright_scrape_aliexpress(search)
    google_task    = scrape_google_shopping(search)
    pricerunner_task = scrape_pricerunner(search)
    watchcount_task  = scrape_watchcount(keyword)
    comp_task      = get_competition_data(keyword, memory)

    ali_prices, google_prices, pricerunner_prices, watch_data, comp_data = await asyncio.gather(
        ali_task, google_task, pricerunner_task, watchcount_task, comp_task,
        return_exceptions=True
    )

    # Handle any errors from gather
    if isinstance(ali_prices, Exception):        ali_prices = []
    if isinstance(google_prices, Exception):     google_prices = []
    if isinstance(pricerunner_prices, Exception):pricerunner_prices = []
    if isinstance(watch_data, Exception):        watch_data = {}
    if isinstance(comp_data, Exception):         comp_data = {}

    memory["data_source_stats"]["google"]      = memory["data_source_stats"].get("google",0) + (1 if google_prices else 0)
    memory["data_source_stats"]["pricerunner"] = memory["data_source_stats"].get("pricerunner",0) + (1 if pricerunner_prices else 0)
    memory["data_source_stats"]["watchcount"]  = memory["data_source_stats"].get("watchcount",0) + (1 if watch_data.get("watch_count",0) > 0 else 0)

    for item in ebay_items[:10]:
        result = analyse_product(item, ali_prices, comp_data, watch_data, google_prices, pricerunner_prices)
        if result:
            products.append(result)
            trend_icon = "📈" if result["competition_trend"] == "growing_fast" else "📉" if result["competition_trend"] == "shrinking" else "➡"
            log(f"  ✓ {result['ebay_title'][:45]} | £{result['profit_gbp']} profit | {result['competitor_count']} competitors {trend_icon}", "GOOD")
        await asyncio.sleep(0.3)

    await asyncio.sleep(random.uniform(2,4))
    return products, memory

def generate_fallback_products(keyword):
    prompt = f"""List 5 real UK eBay products for "{keyword}" selling well in {get_season()}.
Avoid oversaturated products. JSON only:
[{{"title": "...", "ebay_sell_price": 24.99, "source": "ai_estimate"}}]"""
    return extract_json(ask_ollama(prompt), "array") or []

# ─── AGENT BRAIN ─────────────────────────────────────────────────────────────

def decide_keywords(memory):
    scores    = memory.get("search_scores", {})
    all_terms = memory.get("search_terms", SEARCH_TERMS)
    saturated = memory.get("saturated_keywords", [])
    available = [t for t in all_terms if t not in saturated]

    score_str = "\n".join(
        f"  {t}: £{s:.2f} avg profit" for t,s in
        sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
    ) or "  No data yet"

    comp_str = "\n".join(
        f"  {kw}: {vals[-1].get('count','?')} sellers, trend {vals[-1].get('trend','?')}"
        for kw, vals in list(memory.get("competition_history",{}).items())[-5:]
        if vals
    ) or "  No competition data yet"

    prompt = f"""Autonomous eBay dropshipping agent deciding what to scan.

KEYWORD SCORES: {score_str}
COMPETITION DATA: {comp_str}
SATURATED (skip): {json.dumps(saturated)}
SEASON: {get_season()}
STRATEGY: {(memory.get('strategy_notes') or ['None'])[-1]}

Pick 4 keywords from available list. Prefer high profit, low competition, growing trends.
Avoid saturated markets.

JSON only:
{{"scan": ["kw1","kw2","kw3","kw4"], "add": "new keyword or null", "reason": "one sentence"}}"""

    resp = ask_ollama(prompt, temp=0.5)
    dec  = extract_json(resp, "object")

    if not dec or "scan" not in dec:
        chosen = random.sample(available, min(4, len(available)))
        return {"scan": chosen, "add": None, "reason": "Fallback"}

    log(f"Agent: {dec.get('reason','')}", "AGENT")
    return dec

def self_review(memory):
    log("Self-review...", "AGENT")
    products = memory.get("all_products", [])
    if len(products) < 5: return memory

    recent   = products[-30:]
    profits  = [p.get("profit_gbp",0) for p in recent if p.get("profit_gbp",0) > 0]
    avg      = sum(profits)/len(profits) if profits else 0
    real_pct = sum(1 for p in recent if p.get("real_data")) / max(len(recent),1) * 100
    best     = max(recent, key=lambda x: x.get("profit_gbp",0))
    
    # Competition insights
    growing = [kw for kw, vals in memory.get("competition_history",{}).items() 
               if vals and vals[-1].get("trend") == "growing_fast"]
    shrinking = [kw for kw, vals in memory.get("competition_history",{}).items() 
                 if vals and vals[-1].get("trend") == "shrinking"]

    prompt = f"""Agent self-review.

PERFORMANCE: {len(recent)} products | £{avg:.2f} avg profit | {real_pct:.0f}% real data
BEST: {best.get('ebay_title','')} at £{best.get('profit_gbp',0):.2f}
GROWING COMPETITION: {growing}
SHRINKING COMPETITION (opportunity): {shrinking}
SATURATED: {memory.get('saturated_keywords',[])}
CYCLES: {memory.get('cycle_count',0)}

JSON only:
{{
  "analysis": "2 sentence assessment",
  "drop_keywords": [],
  "add_keywords": [],
  "strategy_note": "one sentence"
}}"""

    resp   = ask_ollama(prompt, temp=0.4)
    review = extract_json(resp, "object")
    if not review: return memory

    log(f"Review: {review.get('analysis','')}", "AGENT")
    for kw in (review.get("drop_keywords") or []):
        if kw in memory["search_terms"] and len(memory["search_terms"]) > 5:
            memory["search_terms"].remove(kw)
    for kw in (review.get("add_keywords") or []):
        if kw not in memory["search_terms"]:
            memory["search_terms"].append(kw)
    if review.get("strategy_note"):
        memory["strategy_notes"].append(f"[Cycle {memory['cycle_count']}] {review['strategy_note']}")
    memory["last_review"] = datetime.now().isoformat()

    send_telegram(
        f"🤖 <b>Self-Review Cycle {memory['cycle_count']}</b>\n"
        f"{review.get('analysis','')}\n"
        f"Real data: {real_pct:.0f}% | Avg profit: £{avg:.2f}\n"
        f"Saturated markets avoided: {len(memory.get('saturated_keywords',[]))}"
    )
    return memory

# ─── OUTPUT ──────────────────────────────────────────────────────────────────

def append_listings(products):
    lines = [f"\n{'='*60}", f"CYCLE — {datetime.now().strftime('%d/%m/%Y %H:%M')}", f"{'='*60}\n"]
    for p in sorted(products, key=lambda x: x.get("profit_gbp",0), reverse=True):
        tag    = "✓ REAL" if p.get("real_data") else "~ AI"
        trend  = {"growing_fast":"📈 GROWING FAST","growing":"📈 Growing","shrinking":"📉 Shrinking","stable":"➡ Stable"}.get(p.get("competition_trend",""),"")
        risk   = f"\n⚠  RISK: {p['risk_flag']}" if p.get("risk_flag") else ""
        lines += [
            f"[{tag}] {p.get('ebay_title','')}",
            f"Buy £{p['price_gbp']} → Sell £{p['ebay_sell_price']} → PROFIT £{p['profit_gbp']}",
            f"Competitors: {p.get('competitor_count','?')} | Avg competitor price: £{p.get('avg_competitor_price','?')} | {trend}",
            f"Saturation: {p.get('saturation_level','?')} | Watch count: {p.get('watch_count',0)} | Demand: {p.get('demand_score','?')}/10",
            f"Season: {p.get('seasonal_relevance','?')} | Data sources: {p.get('data_sources',0)}",
            f"Edge: {p.get('unique_angle','')}",
            f"{risk}",
            f"\neBay Title:\n{p.get('ebay_title','')}",
            f"\nDescription:\n{p.get('ebay_description','')}",
        ]
        pts = p.get("key_selling_points",[])
        if pts: lines.append("Points:\n" + "\n".join(f"  • {x}" for x in pts))
        lines += [f"\nAliExpress: {p.get('aliexpress_url','')}", f"eBay: {p.get('ebay_url','')}", "\n"+"-"*40+"\n"]
    with open(LISTINGS_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

def update_scores(memory, products, keywords):
    for kw in keywords:
        kw_prods = [p for p in products if kw.lower() in p.get("title","").lower()]
        if kw_prods:
            avg = sum(p.get("profit_gbp",0) for p in kw_prods) / len(kw_prods)
            old = memory["search_scores"].get(kw, avg)
            memory["search_scores"][kw] = round(old*0.7 + avg*0.3, 2)

# ─── MAIN LOOP ───────────────────────────────────────────────────────────────

async def run_cycle_async(memory):
    cycle = memory["cycle_count"] + 1
    memory["cycle_count"] = cycle
    log(f"{'='*50}")
    log(f"CYCLE {cycle} | Season: {get_season()} | {datetime.now().strftime('%d/%m %H:%M')}")
    log(f"{'='*50}")

    decision = decide_keywords(memory)
    keywords = decision.get("scan", SEARCH_TERMS[:4])
    new_kw   = decision.get("add")
    if new_kw and new_kw not in memory["search_terms"]:
        memory["search_terms"].append(new_kw)
        log(f"Added: {new_kw}", "AGENT")

    all_products = []
    for kw in keywords:
        prods, memory = await scan_keyword_async(kw, memory)
        all_products.extend(prods)

    if all_products:
        memory["all_products"].extend(all_products)
        memory["total_found"] = len(memory["all_products"])
        update_scores(memory, all_products, keywords)
        best = max(all_products, key=lambda x: x.get("profit_gbp",0))
        if not memory["best_ever"] or best["profit_gbp"] > memory["best_ever"].get("profit_gbp",0):
            memory["best_ever"] = best
        append_listings(all_products)
        real_n = sum(1 for p in all_products if p.get("real_data"))

        top3 = sorted(all_products, key=lambda x: x.get("profit_gbp",0), reverse=True)[:3]
        msg = f"🛒 <b>Cycle {cycle}</b> | {len(all_products)} products | {real_n} real\n\n"
        for p in top3:
            trend = "📈" if "growing" in p.get("competition_trend","") else ""
            msg += f"{'✓' if p.get('real_data') else '~'} {p.get('ebay_title','')[:45]}\n"
            msg += f"  £{p['profit_gbp']} profit | {p.get('competitor_count','?')} competitors {trend}\n"
        send_telegram(msg)
    else:
        send_telegram(f"🔄 Cycle {cycle}: Nothing profitable. Adjusting next cycle.")

    if cycle % REVIEW_EVERY_N == 0:
        memory = self_review(memory)

    save_memory(memory)
    log(f"Cycle {cycle} done. {len(all_products)} products.", "GOOD")
    return memory

def main():
    print("""
╔══════════════════════════════════════════════════════╗
║  Autonomous eBay Dropship Agent v3.0                 ║
║  Competition Tracker + Multi-Source Data             ║
╚══════════════════════════════════════════════════════╝
""")
    log(f"Model: {OLLAMA_MODEL} | Interval: {SCAN_INTERVAL_MIN}min | Min profit: £{MIN_PROFIT}")
    log(f"Season: {get_season()} | Sources: eBay + AliExpress + Google + PriceRunner + WatchCount")
    if EBAY_APP_ID:
        log(f"eBay API: {EBAY_ENV.upper()} keyset ({EBAY_APP_ID})", "GOOD")
        if EBAY_ENV == "sandbox":
            log("Sandbox has no real sold-listing data — findCompletedItems will likely return empty. Set EBAY_ENV=production in .env for real data.", "WARN")
    else:
        log(f"eBay API: no {EBAY_ENV.upper()} App ID set in .env — falling back to scraping/AI estimates", "WARN")

    try:
        requests.get("http://localhost:11434/api/tags", timeout=4)
        log("Ollama connected", "GOOD")
    except:
        log("Ollama not reachable!", "WARN")

    memory = load_memory()
    log(f"Memory loaded | Cycles: {memory.get('cycle_count',0)} | Products: {memory.get('total_found',0)}")
    log(f"Saturated markets (skipping): {memory.get('saturated_keywords',[])}")
    send_telegram(f"🚀 <b>Dropship Agent v3.0</b>\n5 data sources | Competition tracking | {get_season()} mode")

    while True:
        try:
            memory = asyncio.run(run_cycle_async(memory))
            nxt = datetime.now() + timedelta(minutes=SCAN_INTERVAL_MIN)
            log(f"Sleeping until {nxt.strftime('%H:%M')}...")
            time.sleep(SCAN_INTERVAL_MIN * 60)
        except KeyboardInterrupt:
            log("Stopped.")
            save_memory(memory)
            send_telegram("⏹ Agent v3.0 stopped.")
            break
        except Exception as e:
            log(f"Error: {e}", "WARN")
            time.sleep(300)

if __name__ == "__main__":
    main()