"""
eBay Browse API client — the OFFICIAL source for ACTIVE listings.

Why this exists: scraping is fragile (layout changes, bot detection). The Browse
API is a sanctioned REST endpoint with no HTML parsing. Access is easy to obtain
(default ~5,000 calls/day, raised via eBay's Application Growth Check).

    GET {base}/buy/browse/v1/item_summary/search
    Auth: OAuth2 client-credentials "Application" token (scope api_scope)

Configuration (env vars):
    EBAY_CLIENT_ID       — App ID from developer.ebay.com   (required)
    EBAY_CLIENT_SECRET   — Cert ID from developer.ebay.com   (required)
    EBAY_MARKETPLACE_ID  — e.g. EBAY_US (default)
    EBAY_ENV             — "production" (default) or "sandbox"

IMPORTANT: Browse returns ACTIVE listings only. Sold/completed sales (the data
this product is built on) require the Marketplace Insights API, a limited-release
product gated behind eBay's Application Growth Check. Until that access is granted,
keep using app/scraper.py for sold data; use this module for active-listing
enrichment and as an official, low-fragility data source.
"""

import base64
import logging
import os
import time

import httpx

from app.schemas import BrowseListing

log = logging.getLogger(__name__)

_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")
_MARKETPLACE = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")
_ENV = os.environ.get("EBAY_ENV", "production").lower()

_BASE = "https://api.sandbox.ebay.com" if _ENV == "sandbox" else "https://api.ebay.com"
_OAUTH_URL = f"{_BASE}/identity/v1/oauth2/token"
_SEARCH_URL = f"{_BASE}/buy/browse/v1/item_summary/search"
_SCOPE = "https://api.ebay.com/oauth/api_scope"

# Application token cache. Tokens are valid ~2h; we refresh a minute early.
_token_cache: dict[str, float | str] = {"token": "", "expires_at": 0.0}


def browse_configured() -> bool:
    return bool(_CLIENT_ID and _CLIENT_SECRET)


async def _get_token(client: httpx.AsyncClient) -> str | None:
    now = time.time()
    cached = _token_cache["token"]
    if cached and float(_token_cache["expires_at"]) - 60 > now:
        return str(cached)

    if not browse_configured():
        return None

    basic = base64.b64encode(f"{_CLIENT_ID}:{_CLIENT_SECRET}".encode()).decode()
    try:
        resp = await client.post(
            _OAUTH_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": _SCOPE},
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as e:
        log.warning("eBay OAuth token request failed: %s", e)
        return None

    token = payload.get("access_token")
    if not token:
        log.warning("eBay OAuth response had no access_token")
        return None

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + float(payload.get("expires_in", 7200))
    return token


def _parse_summary(item: dict) -> BrowseListing | None:
    item_id = item.get("itemId")
    title = item.get("title")
    if not item_id or not title:
        return None

    price = item.get("price") or {}
    image = item.get("image") or {}
    seller = item.get("seller") or {}
    location = item.get("itemLocation") or {}
    loc_parts = [location.get("city"), location.get("stateOrProvince"), location.get("country")]
    item_location = ", ".join(p for p in loc_parts if p) or None

    price_value = price.get("value")
    return BrowseListing(
        item_id=str(item_id),
        title=title,
        price=float(price_value) if price_value is not None else None,
        currency=price.get("currency"),
        condition=item.get("condition"),
        image_url=image.get("imageUrl"),
        item_web_url=item.get("itemWebUrl"),
        seller=seller.get("username"),
        item_location=item_location,
    )


async def search_active_listings(
    query: str,
    limit: int = 50,
    category_ids: str | None = None,
    extra_filter: str | None = None,
) -> list[BrowseListing]:
    """Search active eBay listings via the official Browse API.

    Returns [] when the API is not configured or the request fails — callers
    should treat an empty list as "no official data available".
    """
    if not browse_configured():
        log.info("Browse API not configured (EBAY_CLIENT_ID/SECRET unset)")
        return []

    params: dict[str, str | int] = {"q": query, "limit": max(1, min(limit, 200))}
    if category_ids:
        params["category_ids"] = category_ids
    if extra_filter:
        params["filter"] = extra_filter

    async with httpx.AsyncClient(timeout=20) as client:
        token = await _get_token(client)
        if not token:
            return []

        try:
            resp = await client.get(
                _SEARCH_URL,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": _MARKETPLACE,
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as e:
            log.warning("Browse API search failed: %s", e)
            return []

    summaries = payload.get("itemSummaries") or []
    results: list[BrowseListing] = []
    for item in summaries:
        listing = _parse_summary(item)
        if listing is not None:
            results.append(listing)
    log.info("Browse API returned %d active listings for: %s", len(results), query)
    return results
