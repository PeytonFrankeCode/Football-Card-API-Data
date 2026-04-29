import asyncio
import re
from datetime import datetime
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Serialize eBay requests — simultaneous hits trigger Akamai rate-limiting
_sem = asyncio.Semaphore(1)

HEADERS = {
    "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua":                 '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile":          "?0",
    "Sec-Ch-Ua-Platform":        '"Windows"',
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "cross-site",
    "Sec-Fetch-User":            "?1",
    "Cache-Control":             "max-age=0",
    "Referer":                   "https://www.google.com/",
}


class ScrapeRequest(BaseModel):
    query: str
    max_pages: int = 1


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    async with _sem:
        results = await _fetch_pages(req.query, min(req.max_pages, 3))
    return results


async def _fetch_pages(query: str, max_pages: int) -> list:
    all_results = []
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        for page in range(1, max_pages + 1):
            params = urlencode({
                "_nkw": query, "LH_Complete": "1", "LH_Sold": "1",
                "_pgn": page, "_ipg": "60",
            })
            try:
                r = await client.get(f"https://www.ebay.com/sch/i.html?{params}")
                r.raise_for_status()
            except Exception:
                break

            body = r.text
            if any(x in body.lower() for x in ["pardon our interruption", "captcha", "robot check", "access denied"]):
                break

            items = _parse(body)
            if not items:
                break
            all_results.extend(items)

            if page < max_pages:
                await asyncio.sleep(1.0)

    return all_results


def _parse(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")

    # Try standard list-view classes first, then card/grid-view
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
