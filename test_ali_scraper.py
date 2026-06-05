"""Tests for ali_scraper.py — run before implementing."""

import os
import tempfile
from unittest.mock import patch, MagicMock

# --- parse_products ---

def test_parse_products_returns_list():
    from ali_scraper import parse_products
    result = parse_products("<html></html>")
    assert isinstance(result, list)

def test_parse_products_extracts_title_and_price():
    from ali_scraper import parse_products
    # Minimal HTML matching AliExpress product card structure
    html = """
    <div class="search-item-card-wrapper-gallery">
      <div class="manhattan--titleText--WccSjUS">Wireless Earbuds Pro</div>
      <div class="manhattan--price-sale--U-S3y19">US $12.99</div>
    </div>
    <div class="search-item-card-wrapper-gallery">
      <div class="manhattan--titleText--WccSjUS">LED Strip Lights 5m</div>
      <div class="manhattan--price-sale--U-S3y19">US $7.49</div>
    </div>
    """
    result = parse_products(html)
    assert len(result) == 2
    assert result[0]["title"] == "Wireless Earbuds Pro"
    assert result[0]["price"] == "US $12.99"
    assert result[1]["title"] == "LED Strip Lights 5m"

def test_parse_products_caps_at_10():
    from ali_scraper import parse_products
    # 12 product cards — should return at most 10
    card = """<div class="search-item-card-wrapper-gallery">
      <div class="manhattan--titleText--WccSjUS">Product</div>
      <div class="manhattan--price-sale--U-S3y19">US $1.00</div>
    </div>"""
    html = card * 12
    result = parse_products(html)
    assert len(result) <= 10

# --- save_products ---

def test_save_products_writes_file():
    from ali_scraper import save_products
    products = [
        {"title": "Cool Gadget", "price": "US $9.99"},
        {"title": "Phone Case", "price": "US $2.50"},
    ]
    with tempfile.NamedTemporaryFile(mode="r", suffix=".txt", delete=False) as f:
        path = f.name
    try:
        save_products(products, path)
        content = open(path, encoding="utf-8").read()
        assert "Cool Gadget" in content
        assert "US $9.99" in content
        assert "Phone Case" in content
    finally:
        os.unlink(path)

def test_save_products_numbers_each_entry():
    from ali_scraper import save_products
    products = [{"title": "Item A", "price": "US $1.00"}]
    with tempfile.NamedTemporaryFile(mode="r", suffix=".txt", delete=False) as f:
        path = f.name
    try:
        save_products(products, path)
        content = open(path, encoding="utf-8").read()
        assert "1." in content
    finally:
        os.unlink(path)
