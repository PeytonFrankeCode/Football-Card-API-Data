"""
eBay completed/sold listing scraper.

Routes requests through ScraperAPI (when SCRAPER_API_KEY is set) to bypass
eBay's datacenter IP blocks on cloud hosts like Render.
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from urllib.parse import quote_plus, urlencode

import httpx
from bs4 import BeautifulSoup

from app.schemas import ScrapedListing

log = logging.getLogger(__name__)

_SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "")

_EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _build_url(query: str, page: int) -> str:
    params = {
        "_nkw": query,
        "LH_Complete": "1",
        "LH_Sold": "1",
        "_pgn": page,
        "_ipg": "60",
    }
    ebay_url = f"{_EBAY_SEARCH_URL}?{urlencode(params)}"

    if _SCRAPER_API_KEY:
        return (
            f"http://api.scraperapi.com"
            f"?api_key={_SCRAPER_API_KEY}"
            f"&url={quote_plus(ebay_url)}"
            f"&render=false"
        )
    return ebay_url


def _parse_price(text: str) -> float | None:
    match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    if match:
        try:
            return float(match.group().replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_date(text: str) -> datetime | None:
    cleaned = re.sub(r"(Sold\s*|sold\s*)", "", text).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _parse_page(html: str) -> list[ScrapedListing]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[ScrapedListing] = []

    for item in soup.select(".s-item, .s-item__pl-on-bottom"):
        title_el = item.select_one(".s-item__title")
        if not title_el or "Shop on eBay" in title_el.get_text():
            continue

        title = title_el.get_text(strip=True)

        price_el = item.select_one(".s-item__price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price = _parse_price(price_text.split(" to ")[0])
        if price is None:
            continue

        link_el = item.select_one("a.s-item__link")
        url = link_el["href"] if link_el and link_el.get("href") else None
        if not url:
            continue
        url = url.split("?")[0]

        sale_date: datetime | None = None
        for selector in (
            ".s-item__title--tagblock .POSITIVE",
            ".s-item__end-time",
            ".POSITIVE",
        ):
            date_el = item.select_one(selector)
            if date_el:
                sale_date = _parse_date(date_el.get_text(strip=True))
                if sale_date:
                    break

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
    """Scrape eBay sold listings. Uses ScraperAPI proxy when SCRAPER_API_KEY is set."""
    all_results: list[ScrapedListing] = []
    using_proxy = bool(_SCRAPER_API_KEY)
    log.info("Scraping eBay (proxy=%s) for: %s", using_proxy, query)

    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=30) as client:
        for page in range(1, max_pages + 1):
            url = _build_url(query, page)
            try:
                response = await client.get(url)
                log.info("HTTP %s, %d bytes (page %d)", response.status_code, len(response.text), page)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                log.warning("HTTP error: %s", e)
                break
            except httpx.HTTPError as e:
                log.warning("Request error: %s", e)
                break

            if "captcha" in response.text.lower() or "robot check" in response.text.lower():
                log.warning("Bot-detection page returned — add SCRAPER_API_KEY to bypass")
                break

            page_results = _parse_page(response.text)
            log.info("Parsed %d items from page %d", len(page_results), page)
            if not page_results:
                log.info("HTML snippet: %s", response.text[:300].replace("\n", " "))
                break

            all_results.extend(page_results)

            if page < max_pages:
                await asyncio.sleep(1.0)

    return all_results
