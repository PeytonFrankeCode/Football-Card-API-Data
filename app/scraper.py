"""
eBay completed/sold listing scraper.

Fetches sold listings from eBay's public search results page.
eBay's HTML structure can change; selectors are based on current layout.
"""

import asyncio
import re
from datetime import datetime
from urllib.parse import urlencode, quote_plus

import httpx
from bs4 import BeautifulSoup

from app.schemas import ScrapedListing

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"


def _build_url(query: str, page: int) -> str:
    params = {
        "_nkw": query,
        "LH_Complete": "1",   # completed listings
        "LH_Sold": "1",       # sold only
        "_pgn": page,
        "_ipg": "60",         # 60 results per page
    }
    return f"{_EBAY_SEARCH_URL}?{urlencode(params)}"


def _parse_price(text: str) -> float | None:
    match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    if match:
        try:
            return float(match.group().replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_date(text: str) -> datetime | None:
    """Parse eBay sold date strings like 'Apr 10, 2025' or 'Sold  Apr 10, 2025'."""
    cleaned = re.sub(r"(Sold\s*|sold\s*)", "", text).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _parse_page(html: str) -> list[ScrapedListing]:
    soup = BeautifulSoup(html, "lxml")
    results: list[ScrapedListing] = []

    for item in soup.select(".s-item"):
        # Skip the ghost "Shop on eBay" placeholder item
        title_el = item.select_one(".s-item__title")
        if not title_el or "Shop on eBay" in title_el.get_text():
            continue

        title = title_el.get_text(strip=True)

        # Price — prefer the primary price, skip "to" ranges by taking first
        price_el = item.select_one(".s-item__price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        # Handle price ranges like "$10.00 to $20.00" — take the first number
        price = _parse_price(price_text.split(" to ")[0])
        if price is None:
            continue

        # URL
        link_el = item.select_one("a.s-item__link")
        url = link_el["href"] if link_el and link_el.get("href") else None
        if not url:
            continue
        # Strip eBay tracking params — keep base item URL
        url = url.split("?")[0]

        # Sold date — eBay puts it in a span with class containing "POSITIVE" or
        # in .s-item__end-time / .s-item__detail--secondary
        sale_date: datetime | None = None
        for selector in (
            ".s-item__end-time",
            ".POSITIVE",
            ".s-item__detail .POSITIVE",
        ):
            date_el = item.select_one(selector)
            if date_el:
                sale_date = _parse_date(date_el.get_text(strip=True))
                if sale_date:
                    break

        # Condition
        condition_el = item.select_one(".SECONDARY_INFO")
        condition = condition_el.get_text(strip=True) if condition_el else None

        results.append(
            ScrapedListing(
                title=title,
                sale_price=price,
                sale_date=sale_date,
                condition=condition,
                listing_url=url,
            )
        )

    return results


async def scrape_sold_listings(query: str, max_pages: int = 1) -> list[ScrapedListing]:
    """Async: scrape eBay sold listings for *query* across *max_pages* pages."""
    all_results: list[ScrapedListing] = []

    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=15) as client:
        for page in range(1, max_pages + 1):
            url = _build_url(query, page)
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError:
                break

            page_results = _parse_page(response.text)
            if not page_results:
                break  # no more results

            all_results.extend(page_results)

            if page < max_pages:
                await asyncio.sleep(1.5)  # be polite between pages

    return all_results
