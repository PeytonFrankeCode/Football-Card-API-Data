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

# Cloudflare Worker proxy URL — set CF_WORKER_URL env var on Render.
# Optional shared secret — set CF_WORKER_SECRET to match the Worker's CF_SECRET.
_CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").rstrip("/")
if _CF_WORKER_URL and not _CF_WORKER_URL.startswith("http"):
    _CF_WORKER_URL = "https://" + _CF_WORKER_URL
_CF_WORKER_SECRET = os.environ.get("CF_WORKER_SECRET", "")

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


def _build_url(query: str, page: int) -> tuple[str, dict]:
    """Return (url, extra_headers) — routes through CF Worker when configured."""
    params = {
        "_nkw": query,
        "LH_Complete": "1",
        "LH_Sold": "1",
        "_pgn": page,
        "_ipg": "60",
    }
    ebay_url = f"{_EBAY_SEARCH_URL}?{urlencode(params)}"

    if _CF_WORKER_URL:
        proxy_url = f"{_CF_WORKER_URL}?url={quote_plus(ebay_url)}"
        extra = {"X-Proxy-Secret": _CF_WORKER_SECRET} if _CF_WORKER_SECRET else {}
        return proxy_url, extra

    return ebay_url, {}


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
    """Scrape eBay sold listings. Routes through Cloudflare Worker when CF_WORKER_URL is set."""
    all_results: list[ScrapedListing] = []
    log.info("Scraping eBay (cf_worker=%s) for: %s", bool(_CF_WORKER_URL), query)

    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=30) as client:
        for page in range(1, max_pages + 1):
            url, extra_headers = _build_url(query, page)
            try:
                response = await client.get(url, headers=extra_headers)
                log.info("HTTP %s, %d bytes (page %d)", response.status_code, len(response.text), page)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                log.warning("HTTP error: %s", e)
                break
            except httpx.HTTPError as e:
                log.warning("Request error: %s", e)
                break

            if "captcha" in response.text.lower() or "robot check" in response.text.lower():
                log.warning("Bot-detection page — set CF_WORKER_URL to route through Cloudflare")
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
