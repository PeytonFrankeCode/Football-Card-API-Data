from __future__ import annotations
import os
import random
import re
import threading
import time
from datetime import datetime
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from curl_cffi.requests import Session
from flask import Flask, request, jsonify

app = Flask(__name__)

HEADERS = {
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.ebay.com/",
    "DNT":             "1",
}

# ── Proxy configuration ───────────────────────────────────────────────────────
# Vercel runs on datacenter IPs that eBay/Akamai blocks by reputation. curl-cffi
# fixes our TLS fingerprint but not our IP, so the durable fix is a residential
# proxy. Two ways to configure one (checked in this order):
#   1. SCRAPER_PROXY_URL  — any proxy URL, e.g. http://user:pass@host:port
#   2. SCRAPER_API_KEY    — ScraperAPI proxy mode (residential pool + our own
#                           curl-cffi impersonation). Tunable with:
#                           SCRAPER_API_COUNTRY (default us),
#                           SCRAPER_API_PREMIUM / SCRAPER_API_ULTRA = true
# With no proxy configured we fall back to a direct (often-blocked) request.
_PROXY_URL = os.environ.get("SCRAPER_PROXY_URL", "").strip()
_SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "").strip()
_SCRAPER_API_COUNTRY = os.environ.get("SCRAPER_API_COUNTRY", "us")
_SCRAPER_API_PREMIUM = os.environ.get("SCRAPER_API_PREMIUM", "").lower() in ("1", "true", "yes")
_SCRAPER_API_ULTRA = os.environ.get("SCRAPER_API_ULTRA", "").lower() in ("1", "true", "yes")

# Rotate the browser we impersonate; fall back to chrome120 if a target is
# unsupported by the installed curl-cffi build.
_IMPERSONATE = ["chrome124", "chrome120", "chrome116", "safari17_0"]

_MAX_RETRIES = 3
_INITIAL_BACKOFF = 1.5


def proxy_configured() -> bool:
    return bool(_PROXY_URL or _SCRAPER_API_KEY)


def _build_proxy(session_number: int) -> str | None:
    """Build a proxy URL. For ScraperAPI we pin a session_number so the homepage
    warm-up and the searches reuse the same residential IP, and rotating it
    (on reset) yields a fresh IP."""
    if _PROXY_URL:
        return _PROXY_URL
    if _SCRAPER_API_KEY:
        opts = [f"country_code={_SCRAPER_API_COUNTRY}", f"session_number={session_number}"]
        if _SCRAPER_API_ULTRA:
            opts.append("ultra_premium=true")
        elif _SCRAPER_API_PREMIUM:
            opts.append("premium=true")
        username = "scraperapi." + ".".join(opts)
        return f"http://{username}:{_SCRAPER_API_KEY}@proxy-server.scraperapi.com:8001"
    return None


# ── Persistent session (reused across requests in the same Lambda instance) ──
_session: Session | None = None
_session_born: float = 0.0
_session_proxies: dict | None = None
_SESSION_TTL = 8 * 60  # refresh after 8 minutes

# ── Request serialization + rate limiting ────────────────────────────────────
_scrape_lock = threading.Lock()
_last_scrape_at: float = 0.0
# With a rotating proxy we don't need to throttle ourselves as hard, since each
# request can come from a different IP.
_MIN_INTERVAL = 1.5 if proxy_configured() else 4.0


def _new_session() -> Session:
    for target in _IMPERSONATE:
        try:
            return Session(impersonate=target)
        except Exception:
            continue
    return Session(impersonate="chrome120")


def _get_session() -> Session:
    global _session, _session_born, _session_proxies
    now = time.time()
    if _session is None or (now - _session_born) > _SESSION_TTL:
        if _session:
            try:
                _session.close()
            except Exception:
                pass
        s = _new_session()
        _session_proxies = None
        proxy = _build_proxy(random.randint(1, 9_999_999))
        if proxy:
            # ScraperAPI proxy mode terminates TLS, so cert verification is off.
            _session_proxies = {"http": proxy, "https": proxy}
        else:
            # No proxy: warm up cookies against the homepage (direct mode only).
            try:
                s.get("https://www.ebay.com", headers=HEADERS, timeout=12)
                time.sleep(random.uniform(1.5, 2.5))
            except Exception:
                pass
        _session = s
        _session_born = time.time()
    return _session


def _reset_session() -> None:
    """Drop the session so the next request gets fresh cookies and, when
    proxying, a fresh residential IP (new session_number)."""
    global _session, _session_born, _session_proxies
    if _session:
        try:
            _session.close()
        except Exception:
            pass
    _session = None
    _session_born = 0.0
    _session_proxies = None


def _get(url: str):
    s = _get_session()
    kwargs = {"headers": HEADERS, "timeout": 25}
    if _session_proxies:
        kwargs["proxies"] = _session_proxies
        kwargs["verify"] = False  # required for ScraperAPI proxy mode
    return s.get(url, **kwargs)


def _is_blocked(html: str) -> bool:
    lower = html.lower()
    return any(x in lower for x in ("pardon our interruption", "captcha", "robot check", "access denied"))


# Optional shared secret: if SCRAPER_SECRET is set, callers must send it as the
# X-Scraper-Secret header. Lets you keep the public scraper endpoint private to
# your Worker.
_SCRAPER_SECRET = os.environ.get("SCRAPER_SECRET", "")


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
@app.route("/api/scrape", methods=["GET"])
def health():
    if request.args.get("debug") != "true":
        return jsonify({"status": "ok", "proxy_configured": proxy_configured()})
    params = urlencode({
        "_nkw": "mahomes prizm", "LH_Complete": "1", "LH_Sold": "1",
        "_pgn": "1", "_ipg": "60",
    })
    try:
        r = _get(f"https://www.ebay.com/sch/i.html?{params}")
        body = r.text
        blocked = _is_blocked(body)
        if blocked:
            _reset_session()
        items = _parse(body)
        return jsonify({
            "http_status": r.status_code,
            "proxy_configured": proxy_configured(),
            "blocked": blocked,
            "parsed_count": len(items),
            "html_snippet": body[:500],
        })
    except Exception as e:
        return jsonify({"error": str(e), "proxy_configured": proxy_configured()})


@app.route("/", methods=["POST"])
@app.route("/scrape", methods=["POST"])
@app.route("/api/scrape", methods=["POST"])
def scrape():
    global _last_scrape_at
    if _SCRAPER_SECRET and request.headers.get("X-Scraper-Secret") != _SCRAPER_SECRET:
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    query = body.get("query", "")
    max_pages = min(int(body.get("max_pages", 1)), 2)

    acquired = _scrape_lock.acquire(timeout=25)
    if not acquired:
        return jsonify([]), 503
    try:
        gap = _MIN_INTERVAL - (time.time() - _last_scrape_at)
        if gap > 0:
            time.sleep(gap)
        results = _fetch_pages(query, max_pages)
        _last_scrape_at = time.time()
    except Exception:
        results = []
    finally:
        _scrape_lock.release()
    return jsonify(results)


def _fetch_page(query: str, page: int) -> list | None:
    """Fetch and parse one page, retrying through fresh IPs on a block.

    Returns the parsed listings, or None if every attempt was blocked/failed.
    """
    params = urlencode({
        "_nkw": query, "LH_Complete": "1", "LH_Sold": "1",
        "_pgn": page, "_ipg": "60",
    })
    url = f"https://www.ebay.com/sch/i.html?{params}"

    backoff = _INITIAL_BACKOFF
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = _get(url)
            text = r.text
        except Exception:
            text = ""

        if text and not _is_blocked(text):
            return _parse(text)

        # Blocked or errored — reset so the next attempt uses a fresh IP/cookies.
        _reset_session()
        if attempt < _MAX_RETRIES:
            time.sleep(backoff + random.uniform(0, 0.8))
            backoff *= 2

    return None


def _fetch_pages(query: str, max_pages: int) -> list:
    all_results = []
    for page in range(1, max_pages + 1):
        items = _fetch_page(query, page)
        if not items:  # None (blocked) or empty (no more results)
            break
        all_results.extend(items)
        if page < max_pages:
            time.sleep(random.uniform(2.0, 3.5))
    return all_results


# ── Parsing ───────────────────────────────────────────────────────────────────

# Grade like "PSA 10", "BGS 9.5", "SGC 9", "CGC 10", "PSA10". Company then number.
_GRADE_RE = re.compile(r"\b(PSA|BGS|BVG|SGC|CGC|CSG|HGA|TAG)\s*\.?\s*(10|\d(?:\.5)?)\b", re.IGNORECASE)
# Listings that pollute price comps.
_JUNK_RE = re.compile(
    r"\b(lot|lots|reprint|re-print|\brp\b|repack|digital|custom|sticker|decal|"
    r"proxy|aceo|novelty|case\s*break|box\s*break|read\s*description)\b",
    re.IGNORECASE,
)
_ITEM_NUMBER_RE = re.compile(r"/itm/(?:[^/]+/)?(\d{6,})")


def _parse_grade(title: str):
    m = _GRADE_RE.search(title)
    if not m:
        return None, None
    try:
        return m.group(1).upper(), float(m.group(2))
    except ValueError:
        return m.group(1).upper(), None


def _is_junk(title: str) -> bool:
    return bool(_JUNK_RE.search(title))


def _item_number(url: str):
    m = _ITEM_NUMBER_RE.search(url)
    return m.group(1) if m else None


def _parse(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    raw = soup.select("li.s-item") or soup.select("li.s-card")
    results = []

    for item in raw:
        title_el = item.select_one(".s-item__title, .s-card__title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or "Shop on eBay" in title:
            continue
        if _is_junk(title):
            continue

        price_el = item.select_one(".s-item__price, .s-card__price")
        if not price_el:
            continue
        price = _parse_price(price_el.get_text(strip=True).split(" to ")[0])
        if price is None:
            continue

        link_el = item.select_one("a.s-item__link, a.s-card__link")
        url = link_el.get("href", "").split("?")[0] if link_el else None
        if not url:
            continue

        sale_date = None
        for sel in [".POSITIVE", ".s-item__caption--signal", ".s-item__subtitle", ".s-card__subtitle"]:
            date_el = item.select_one(sel)
            if date_el:
                sale_date = _parse_date(date_el.get_text(strip=True))
                if sale_date:
                    break

        cond_el = item.select_one(".SECONDARY_INFO, .s-item__subtitle, .s-card__secondary-info")
        condition = cond_el.get_text(strip=True) if cond_el else None

        img_el = item.select_one("img")
        img_url = (img_el.get("data-defer-load") or img_el.get("src")) if img_el else None

        grade_company, grade = _parse_grade(title)

        results.append({
            "title":         title,
            "sale_price":    price,
            "sale_date":     sale_date.isoformat() if sale_date else None,
            "condition":     condition,
            "listing_url":   url,
            "image_url":     img_url,
            "item_number":   _item_number(url),
            "grade":         grade,
            "grade_company": grade_company,
        })

    return results


def _parse_price(text: str) -> float | None:
    m = re.search(r"\d+\.?\d*", text.replace(",", ""))
    return float(m.group()) if m else None


def _parse_date(text: str) -> datetime | None:
    cleaned = re.sub(r"sold\s*", "", text, flags=re.IGNORECASE).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None
