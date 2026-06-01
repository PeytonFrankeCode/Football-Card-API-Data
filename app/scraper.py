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
import random
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
# ScraperAPI tuning. Datacenter IPs (Render/CF/Vercel) get blocked by eBay's
# Akamai protection regardless of headers, so the durable fix is residential
# proxies. These knobs let you enable them without a redeploy.
_SCRAPER_API_COUNTRY = os.environ.get("SCRAPER_API_COUNTRY", "us")
_SCRAPER_API_PREMIUM = os.environ.get("SCRAPER_API_PREMIUM", "").lower() in ("1", "true", "yes")
_SCRAPER_API_ULTRA = os.environ.get("SCRAPER_API_ULTRA", "").lower() in ("1", "true", "yes")
_SCRAPER_API_RENDER = os.environ.get("SCRAPER_API_RENDER", "").lower() in ("1", "true", "yes")

_CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").rstrip("/")
if _CF_WORKER_URL and not _CF_WORKER_URL.startswith("http"):
    _CF_WORKER_URL = "https://" + _CF_WORKER_URL
_CF_WORKER_SECRET = os.environ.get("CF_WORKER_SECRET", "")

_EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"

# Retry tuning. eBay/Akamai and proxy providers return these on transient load,
# and a bot-detection page is also treated as retryable (a rotating proxy will
# hand us a fresh IP on the next attempt).
_MAX_RETRIES = 3
_INITIAL_BACKOFF = 2.0  # seconds; doubles each retry (2s, 4s, 8s) + jitter
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_ITEM_NUMBER_RE = re.compile(r"/itm/(?:[^/]+/)?(\d{6,})")

# Grade like "PSA 10", "BGS 9.5", "SGC 9", "CGC 10", "BVG 8.5". Company then number.
_GRADE_RE = re.compile(r"\b(PSA|BGS|BVG|SGC|CGC|CSG|HGA|TAG)\s*\.?\s*(10|\d(?:\.5)?)\b", re.IGNORECASE)

# Listings that pollute price comps. Word-boundaried to avoid false hits.
_JUNK_RE = re.compile(
    r"\b(lot|lots|reprint|re-print|\brp\b|repack|digital|custom|sticker|decal|"
    r"proxy|aceo|novelty|case\s*break|box\s*break|read\s*description)\b",
    re.IGNORECASE,
)

# Rotate the User-Agent across attempts; a single static UA is an easy signal.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def _base_headers() -> dict:
    """Realistic browser headers with a rotated User-Agent. Mirrors the richer
    header set the CF Worker proxy already sends."""
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
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
        params = {
            "api_key": _SCRAPER_API_KEY,
            "url": ebay_url,
            "country_code": _SCRAPER_API_COUNTRY,
        }
        # render runs the page's JS (needed if eBay serves a JS challenge);
        # premium / ultra_premium select residential proxy pools that beat
        # datacenter-IP blocking. Only sent when enabled to avoid extra cost.
        if _SCRAPER_API_RENDER:
            params["render"] = "true"
        if _SCRAPER_API_ULTRA:
            params["ultra_premium"] = "true"
        elif _SCRAPER_API_PREMIUM:
            params["premium"] = "true"
        scraper_url = "https://api.scraperapi.com/?" + urlencode(params, quote_via=quote_plus)
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
        or "access denied" in body
    )


async def _fetch_route(client: httpx.AsyncClient, label: str, url: str, headers: dict) -> str | None:
    """Fetch one route with retry + exponential backoff + jitter.

    Retries on transient HTTP status, request errors, AND bot-detection pages
    (a rotating residential proxy serves a fresh IP on the next attempt, and we
    rotate the User-Agent too). Returns usable HTML, or None to fall through to
    the next route.
    """
    backoff = _INITIAL_BACKOFF
    for attempt in range(1, _MAX_RETRIES + 1):
        wait = backoff
        try:
            resp = await client.get(url, headers={**_base_headers(), **headers})
        except httpx.HTTPError as e:
            log.warning("[%s] request error (attempt %d/%d): %s", label, attempt, _MAX_RETRIES, e)
        else:
            if resp.status_code in _RETRYABLE_STATUS:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = float(retry_after)
                log.warning("[%s] HTTP %s (attempt %d/%d)", label, resp.status_code, attempt, _MAX_RETRIES)
            elif resp.status_code != 200:
                log.warning("[%s] HTTP %s, not retryable — falling through", label, resp.status_code)
                return None
            elif _is_bot_page(resp.text):
                log.warning("[%s] bot-detection page (attempt %d/%d), retrying on fresh IP/UA",
                            label, attempt, _MAX_RETRIES)
            else:
                log.info("[%s] HTTP 200, %d bytes", label, len(resp.text))
                return resp.text

        if attempt < _MAX_RETRIES:
            await asyncio.sleep(wait + random.uniform(0, 1.0))  # jitter
            backoff *= 2

    log.warning("[%s] exhausted retries", label)
    return None


async def _fetch_html(client: httpx.AsyncClient, ebay_url: str) -> str | None:
    """Try each transport route in priority order until one returns usable HTML."""
    for label, url, extra_headers in _routes(ebay_url):
        html = await _fetch_route(client, label, url, extra_headers)
        if html is not None:
            return html
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


def parse_grade(title: str) -> tuple[str | None, float | None]:
    """Extract (grade_company, grade) from a listing title, e.g. 'PSA 10' -> ('PSA', 10.0)."""
    match = _GRADE_RE.search(title)
    if not match:
        return None, None
    company = match.group(1).upper()
    try:
        grade = float(match.group(2))
    except ValueError:
        grade = None
    return company, grade


def is_junk(title: str) -> bool:
    """True for lots, reprints, customs, digital, etc. that pollute price comps."""
    return bool(_JUNK_RE.search(title))


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
    if is_junk(title):
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

    grade_company, grade = parse_grade(title)

    return ScrapedListing(
        title=title,
        sale_price=price,
        sale_date=sale_date,
        condition=condition,
        listing_url=url,
        image_url=image_url,
        item_number=_item_number(url),
        grade=grade,
        grade_company=grade_company,
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
                    await asyncio.sleep(1.0 + random.uniform(0, 1.5))  # jitter

    return all_results
