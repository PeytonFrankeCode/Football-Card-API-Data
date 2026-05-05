from __future__ import annotations
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

# ── Persistent session (reused across requests in the same Lambda instance) ──
_session: Session | None = None
_session_born: float = 0.0
_SESSION_TTL = 8 * 60  # refresh after 8 minutes

# ── Request serialization + rate limiting ────────────────────────────────────
_scrape_lock = threading.Lock()
_last_scrape_at: float = 0.0
_MIN_INTERVAL = 4.0  # seconds between any two eBay fetches


def _get_session() -> Session:
    global _session, _session_born
    now = time.time()
    if _session is None or (now - _session_born) > _SESSION_TTL:
        if _session:
            try:
                _session.close()
            except Exception:
                pass
        s = Session(impersonate="chrome120")
        try:
            s.get("https://www.ebay.com", headers=HEADERS, timeout=12)
            time.sleep(random.uniform(1.5, 2.5))
        except Exception:
            pass
        _session = s
        _session_born = time.time()
    return _session


def _reset_session() -> None:
    global _session, _session_born
    if _session:
        try:
            _session.close()
        except Exception:
            pass
    _session = None
    _session_born = 0.0


@app.route("/", methods=["GET"])
@app.route("/api/scrape", methods=["GET"])
def health():
    if request.args.get("debug") != "true":
        return jsonify({"status": "ok"})
    params = urlencode({
        "_nkw": "mahomes prizm", "LH_Complete": "1", "LH_Sold": "1",
        "_pgn": "1", "_ipg": "60",
    })
    try:
        s = _get_session()
        r = s.get(f"https://www.ebay.com/sch/i.html?{params}", headers=HEADERS, timeout=20)
        body = r.text
        blocked = any(x in body.lower() for x in ["pardon our interruption", "captcha", "robot check", "access denied"])
        if blocked:
            _reset_session()
        items = _parse(body)
        return jsonify({
            "http_status": r.status_code,
            "blocked": blocked,
            "parsed_count": len(items),
            "html_snippet": body[:500],
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/", methods=["POST"])
@app.route("/api/scrape", methods=["POST"])
def scrape():
    global _last_scrape_at
    body = request.get_json(silent=True) or {}
    query = body.get("query", "")
    max_pages = min(int(body.get("max_pages", 1)), 2)

    acquired = _scrape_lock.acquire(timeout=25)
    if not acquired:
        return jsonify([]), 503
    try:
        # Enforce minimum gap between eBay fetches
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


def _fetch_pages(query: str, max_pages: int) -> list:
    all_results = []
    s = _get_session()

    for page in range(1, max_pages + 1):
        params = urlencode({
            "_nkw": query, "LH_Complete": "1", "LH_Sold": "1",
            "_pgn": page, "_ipg": "60",
        })
        try:
            r = s.get(f"https://www.ebay.com/sch/i.html?{params}", headers=HEADERS, timeout=20)
        except Exception:
            break

        text = r.text
        lower = text.lower()

        if "pardon our interruption" in lower or "captcha" in lower:
            # Soft block — reset session so next request gets fresh cookies
            _reset_session()
            break
        if "access denied" in lower or "robot check" in lower:
            _reset_session()
            break

        items = _parse(text)
        if not items:
            break
        all_results.extend(items)

        if page < max_pages:
            time.sleep(random.uniform(2.0, 3.5))

    return all_results


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

        results.append({
            "title":       title,
            "sale_price":  price,
            "sale_date":   sale_date.isoformat() if sale_date else None,
            "condition":   condition,
            "listing_url": url,
            "image_url":   img_url,
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
