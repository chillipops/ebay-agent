# eBay Dropship Research Agent

A Python agent that looks for products worth reselling on eBay UK. It compares what an item sells for on eBay with what it costs to buy from AliExpress, checks how much competition there is, and reports the best opportunities.

## What it does

- **Collects market data from several sources:** the official eBay Browse API (OAuth), live eBay UK listings (Playwright), Google Shopping, PriceRunner UK and WatchCount (eBay watcher counts as a demand signal).
- **Matches buy prices** from AliExpress for each product, then works out the likely profit margin.
- **Tracks competition:** number of competing sellers, average price, market saturation and price trends.
- **Uses a local AI model** (via Ollama) to analyse the results and rank products, with a blacklist and seasonal adjustments.
- **Remembers past scans** so it can spot changes over time.
- **Sends alerts** for strong finds via Telegram.

## Files

| File | Purpose |
|---|---|
| `dropship_agent.py` | Main agent: data collection, competition tracking, AI analysis and scan loop |
| `ebay_agent.py` | Earlier research agent using live eBay UK data |
| `ali_scraper.py` | AliExpress price scraper (Playwright) |
| `test_ali_scraper.py` | Tests for the AliExpress scraper |
| `START_EBAY_AGENT.bat` | Windows launcher |

## Setup

1. Install Python 3.11+ and the packages used (requests, beautifulsoup4, python-dotenv, playwright).
2. Run `playwright install` once.
3. Put your eBay API credentials in a `.env` file (never committed). `EBAY_ENV` can be `sandbox` (default, no live data) or `production`.
4. Install Ollama with a local model for the analysis step.
5. Run `START_EBAY_AGENT.bat` or `python dropship_agent.py`.

## Notes

Research tool only: it finds and ranks opportunities but does not buy or list anything automatically.
