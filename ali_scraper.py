"""AliExpress best-seller scraper — saves top 10 products to ali_products.txt.

Uses Playwright to render JavaScript so product cards are fully loaded.
"""

import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://www.aliexpress.com/wholesale?SearchText=best+sellers&SortType=total_tranpro_desc"
OUTPUT_FILE = Path(__file__).parent / "ali_products.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


def parse_products(html: str) -> list[dict]:
    """Extract up to 10 products (title + price) from AliExpress search HTML."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    cards = soup.select("div.search-item-card-wrapper-gallery")
    # Deduplicate by title to handle AliExpress double-rendering
    seen_titles: set[str] = set()

    for card in cards:
        title_el = (
            card.select_one("[class*='titleText']") or
            card.select_one("[class*='title']") or
            card.select_one("a[title]")
        )
        price_el = (
            card.select_one("[class*='price-sale']") or
            card.select_one("[class*='price']") or
            card.select_one("[class*='Price']")
        )

        title = title_el.get_text(strip=True) if title_el else None
        price = price_el.get_text(strip=True) if price_el else None

        if title and title not in seen_titles:
            seen_titles.add(title)
            products.append({
                "title": title,
                "price": price or "Price not listed",
            })
        if len(products) >= 10:
            break

    # Fallback: try new-style AliExpress layout (2024+)
    if not products:
        for card in soup.select("div[class*='card']")[:10]:
            title_el = card.select_one("h3") or card.select_one("[class*='title']")
            price_el = card.select_one("[class*='price']") or card.select_one("[class*='Price']")
            title = title_el.get_text(strip=True) if title_el else None
            price = price_el.get_text(strip=True) if price_el else None
            if title and len(title) > 5:
                products.append({
                    "title": title,
                    "price": price or "Price not listed",
                })

    return products[:10]


def save_products(products: list[dict], filepath: str | Path = OUTPUT_FILE) -> None:
    """Write numbered product list to a text file."""
    lines = ["AliExpress Top 10 Best Sellers", "=" * 40, ""]
    for i, p in enumerate(products, 1):
        lines.append(f"{i}. {p['title']}")
        lines.append(f"   Price: {p['price']}")
        lines.append("")
    Path(filepath).write_text("\n".join(lines), encoding="utf-8")


def scrape(url: str = URL) -> list[dict]:
    """Fetch the page with a real browser and return parsed products."""
    print(f"Launching browser...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        print(f"Fetching: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Wait for product cards to appear
            try:
                page.wait_for_selector(
                    "div.search-item-card-wrapper-gallery, div[class*='card']",
                    timeout=15000,
                )
            except PWTimeout:
                print("  [!] Timed out waiting for product cards — parsing what we have.")

            # Scroll to trigger lazy-load and price rendering
            page.evaluate("window.scrollBy(0, 1500)")
            page.wait_for_timeout(4000)

            html = page.content()
        except PWTimeout:
            print("[!] Page load timed out.")
            html = page.content()
        finally:
            browser.close()

    products = parse_products(html)

    if not products:
        print("[!] No products parsed — AliExpress may have changed its layout or blocked the request.")
        # Save raw HTML for debugging
        debug_path = Path(__file__).parent / "debug.html"
        debug_path.write_text(html, encoding="utf-8")
        print(f"    Raw HTML saved to {debug_path} for inspection.")

    return products


def main() -> None:
    products = scrape()

    if not products:
        print("\nNo products found. Check debug.html for the raw page content.")
        sys.exit(1)

    save_products(products, OUTPUT_FILE)

    print(f"\nSaved {len(products)} products to: {OUTPUT_FILE}\n")
    print("=" * 40)
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
