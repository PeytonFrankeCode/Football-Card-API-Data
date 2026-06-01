"""
eBay completed/sold listing scraper.

Transport routes are tried in priority order, and each route gets its own
retry-with-backoff loop before we fall through to the next one:

1. ScraperAPI  — SCRAPER_API_KEY  (rotating proxies, handles JS challenges)
2. CF Worker   — CF_WORKER_URL    (+ optional CF_WORKER_SECRET)
3. Direct      — no proxy         (usually blocked on cloud hosts)

Every route fetches the same eBay sold-search HTML, which we then parse.
Parsing tries several selector families so a single eBay layout change does
not silently return zero results, and a stable eBay item number is pulled
from each listing URL for de-duplication.

For OFFICIAL active-listing data (no scraping), see app/ebay_browse.py.
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

# Retry tuning. eBay/Akamai and proxy providers return these on transient load.
_MAX_RETRIES = 3
_INITIAL_BACKOFF = 2.0  # seconds; doubles each retry (2s, 4s, 8s)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_ITEM_NUMBER_RE = re.compile(r"/itm/(?:[^/]+/)?(\d{6,})")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _ebay_search_url(query: str, page: int) -> str:
    params = {
        "_nkw": query,
        "LH_Complete": "1",
        "LH_Sold": "1",
        "_pgn": page,
        "_ipg": "60",
    }
    return f"{_EBAY_SEARCH_URL}?{urlencode(params)}"


def _routes(ebay_url: str) -> list[tuple[str, str, dict]]:
    """Return (label, url, extra_headers) for every available transport route,
    in priority order. The caller tries each in turn until one yields usable HTML.
    """
    routes: list[tuple[str, str, dict]] = []

    if _SCRAPER_API_KEY:
        scraper_url = (
            f"https://api.scraperapi.com"
            f"?api_key={_SCRAPER_API_KEY}"
            f"&url={quote_plus(ebay_url)}"
            f"&render=false"
        )
        routes.append(("scraperapi", scraper_url, {}))

    if _CF_WORKER_URL:
        proxy_url = f"{_CF_WORKER_URL}?url={quote_plus(ebay_url)}"
        extra = {"X-Proxy-Secret": _CF_WORKER_SECRET} if _CF_WORKER_SECRET else {}
        routes.append(("cf_worker", proxy_url, extra))

    routes.append(("direct", ebay_url, {}))
    return routes


def active_route() -> str:
    """Human-readable label for the highest-priority configured route."""
    if _SCRAPER_API_KEY:
        return "scraperapi"
    if _CF_WORKER_URL:
        return "cf_worker"
    return "direct"


def _is_bot_page(html: str) -> bool:
    body = html.lower()
    return (
        "captcha" in body
        or "robot check" in body
        or "pardon our interruption" in body
    )


async def _fetch(client: httpx.AsyncClient, url: str, headers: dict) -> httpx.Response | None:
    """GET a URL with retry + exponential backoff on transient failures.

    Returns the successful response, or None if every attempt failed. Honours
    an upstream ``Retry-After`` header when present.
    """
    backoff = _INITIAL_BACKOFF
    for attempt in range(1, _MAX_RETRIES + 1):
        wait = backoff
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            log.warning("Request error (attempt %d/%d): %s", attempt, _MAX_RETRIES, e)
        else:
            if resp.status_code in _RETRYABLE_STATUS:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = float(retry_after)
                log.warning(
                    "HTTP %s (attempt %d/%d), backing off %.1fs",
                    resp.status_code, attempt, _MAX_RETRIES, wait,
                )
            else:
                return resp

        if attempt < _MAX_RETRIES:
            await asyncio.sleep(wait)
            backoff *= 2

    return None


async def _fetch_html(client: httpx.AsyncClient, ebay_url: str) -> str | None:
    """Try each transport route in priority order until one returns usable HTML."""
    for label, url, extra_headers in _routes(ebay_url):
        resp = await _fetch(client, url, extra_headers)
        if resp is None:
            log.warning("Route %s exhausted retries, falling through", label)
            continue

        log.info("Route %s → HTTP %s, %d bytes", label, resp.status_code, len(resp.text))
        if resp.status_code != 200:
            continue
        if _is_bot_page(resp.text):
            log.warning("Route %s hit bot-detection page, falling through", label)
            continue

        return resp.text

    return None


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


def _item_number(url: str) -> str | None:
    match = _ITEM_NUMBER_RE.search(url)
    return match.group(1) if match else None


def _parse_item(item) -> ScrapedListing | None:
    """Parse a single search-result element into a ScrapedListing, or None."""
    # Title — prefer dedicated element, fall back to image alt text
    title_el = item.select_one(".s-card__title, .s-item__title")
    if title_el:
        title = title_el.get_text(strip=True)
    else:
        img = item.select_one("img.s-card__image, img.s-item__image-img")
        title = img.get("alt", "").strip() if img else ""
    if not title or "Shop on eBay" in title:
        return None

    # Price
    price_el = item.select_one(".s-card__price, .s-item__price")
    price_text = price_el.get_text(strip=True) if price_el else ""
    price = _parse_price(price_text.split(" to ")[0])
    if price is None:
        return None

    # URL
    link_el = item.select_one("a.s-card__link, a.s-item__link")
    url = link_el.get("href") if link_el else None
    if not url:
        return None
    url = url.split("?")[0]

    # Sold date — try several candidate selectors
    sale_date: datetime | None = None
    for selector in (
        ".s-card__subtitle",
        ".s-card__date",
        ".s-item__title--tag .POSITIVE",
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
    condition_el = item.select_one(
        ".s-card__secondary-info, .SECONDARY_INFO, [class*='condition']"
    )
    condition = condition_el.get_text(strip=True) if condition_el else None

    # Image — eBay lazy-loads via data-defer-load
    img_el = item.select_one("img.s-card__image, img.s-item__image-img")
    image_url = None
    if img_el:
        image_url = img_el.get("data-defer-load") or img_el.get("src") or None

    return ScrapedListing(
        title=title,
        sale_price=price,
        sale_date=sale_date,
        condition=condition,
        listing_url=url,
        image_url=image_url,
        item_number=_item_number(url),
    )


def _parse_page(html: str) -> list[ScrapedListing]:
    soup = BeautifulSoup(html, "html.parser")

    # eBay rotates its result markup; try selector families newest-first.
    items = soup.select("li.s-card") or soup.select("li.s-item")

    results: list[ScrapedListing] = []
    seen: set[str] = set()
    for item in items:
        listing = _parse_item(item)
        if listing is None:
            continue
        # De-duplicate by stable item number when available, else by URL.
        key = listing.item_number or listing.listing_url
        if key in seen:
            continue
        seen.add(key)
        results.append(listing)

    return results


async def scrape_sold_listings(query: str, max_pages: int = 1) -> list[ScrapedListing]:
    """Scrape eBay sold listings. Tries ScraperAPI → CF Worker → direct, each
    with retry/backoff, and de-duplicates listings across pages."""
    all_results: list[ScrapedListing] = []
    seen: set[str] = set()
    log.info(
        "Scraping eBay (scraperapi=%s, cf_worker=%s) for: %s",
        bool(_SCRAPER_API_KEY), bool(_CF_WORKER_URL), query,
    )

    async with _ebay_semaphore:
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=30) as client:
            for page in range(1, max_pages + 1):
                ebay_url = _ebay_search_url(query, page)
                html = await _fetch_html(client, ebay_url)
                if html is None:
                    log.warning("All routes failed for query %r (page %d)", query, page)
                    break

                page_results = _parse_page(html)
                log.info("Parsed %d items from page %d", len(page_results), page)
                if not page_results:
                    log.info("HTML snippet: %s", html[:300].replace("\n", " "))
                    break

                for listing in page_results:
                    key = listing.item_number or listing.listing_url
                    if key in seen:
                        continue
                    seen.add(key)
                    all_results.append(listing)

                if page < max_pages:
                    await asyncio.sleep(1.0)

    return all_results
