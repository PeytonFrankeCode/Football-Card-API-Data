"""
eBay completed/sold listing scraper.

Priority order for bypassing eBay bot detection:
1. ScraperAPI  — set SCRAPER_API_KEY env var (most reliable, handles JS challenges)
2. CF Worker   — set CF_WORKER_URL env var (fallback)
3. Direct      — no proxy (blocked on cloud hosts)
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

# Only 1 concurrent eBay request — simultaneous searches queue up rather than
# all hitting eBay at once, which is what triggers Akamai rate-limiting.
_ebay_semaphore = asyncio.Semaphore(1)

_SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "")

_CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").rstrip("/")
if _CF_WORKER_URL and not _CF_WORKER_URL.startswith("http"):
    _CF_WORKER_URL = "https://" + _CF_WORKER_URL
_CF_WORKER_SECRET = os.environ.get("CF_WORKER_SECRET", "")

_EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _build_url(query: str, page: int) -> tuple[str, dict]:
    """Return (url, extra_headers). ScraperAPI > CF Worker > direct."""
    params = {
        "_nkw": query,
        "LH_Complete": "1",
        "LH_Sold": "1",
        "_pgn": page,
        "_ipg": "60",
    }
    ebay_url = f"{_EBAY_SEARCH_URL}?{urlencode(params)}"

    if _SCRAPER_API_KEY:
        scraper_url = (
            f"https://api.scraperapi.com"
            f"?api_key={_SCRAPER_API_KEY}"
            f"&url={quote_plus(ebay_url)}"
            f"&render=false"
        )
        log.info("Routing through ScraperAPI")
        return scraper_url, {}

    if _CF_WORKER_URL:
        proxy_url = f"{_CF_WORKER_URL}?url={quote_plus(ebay_url)}"
        extra = {"X-Proxy-Secret": _CF_WORKER_SECRET} if _CF_WORKER_SECRET else {}
        log.info("Routing through CF Worker")
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

    for item in soup.select("li.s-card"):
        # Title — prefer dedicated element, fall back to image alt text
        title_el = item.select_one(".s-card__title")
        if title_el:
            title = title_el.get_text(strip=True)
        else:
            img = item.select_one("img.s-card__image")
            title = img.get("alt", "").strip() if img else ""
        if not title or "Shop on eBay" in title:
            continue

        # Price
        price_el = item.select_one(".s-card__price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price = _parse_price(price_text.split(" to ")[0])
        if price is None:
            continue

        # URL
        link_el = item.select_one("a.s-card__link")
        url = link_el.get("href") if link_el else None
        if not url:
            continue
        url = url.split("?")[0]

        # Sold date — try several candidate selectors
        sale_date: datetime | None = None
        for selector in (
            ".s-card__subtitle",
            ".s-card__date",
            ".su-text--secondary",
            ".POSITIVE",
            "[class*='date']",
            "[class*='sold']",
        ):
            date_el = item.select_one(selector)
            if date_el:
                sale_date = _parse_date(date_el.get_text(strip=True))
                if sale_date:
                    break

        # Condition
        condition_el = item.select_one(".s-card__secondary-info, .SECONDARY_INFO, [class*='condition']")
        condition = condition_el.get_text(strip=True) if condition_el else None

        # Image — eBay lazy-loads via data-defer-load
        img_el = item.select_one("img.s-card__image")
        image_url = None
        if img_el:
            image_url = img_el.get("data-defer-load") or img_el.get("src") or None

        results.append(
            ScrapedListing(
                title=title,
                sale_price=price,
                sale_date=sale_date,
                condition=condition,
                listing_url=url,
                image_url=image_url,
            )
        )

    return results


async def scrape_sold_listings(query: str, max_pages: int = 1) -> list[ScrapedListing]:
    """Scrape eBay sold listings. Uses ScraperAPI > CF Worker > direct, in that order."""
    all_results: list[ScrapedListing] = []
    log.info("Scraping eBay (scraperapi=%s, cf_worker=%s) for: %s", bool(_SCRAPER_API_KEY), bool(_CF_WORKER_URL), query)

    async with _ebay_semaphore:
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
