import json
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app import models, schemas
from app.scraper import scrape_sold_listings

router = APIRouter(prefix="/scrape", tags=["Scrape"])
log = logging.getLogger(__name__)

_CACHE_TTL = timedelta(hours=6)


def _cache_key(query: str) -> str:
    return query.lower().strip()


def _get_cached(db: Session, query: str) -> list[schemas.ScrapedListing] | None:
    row = db.get(models.SearchCache, _cache_key(query))
    if not row:
        return None
    age = datetime.now(timezone.utc) - row.cached_at.replace(tzinfo=timezone.utc)
    if age > _CACHE_TTL:
        return None
    try:
        data = json.loads(row.results_json)
        return [schemas.ScrapedListing(**item) for item in data]
    except Exception:
        return None


def _set_cache(db: Session, query: str, results: list[schemas.ScrapedListing]) -> None:
    def _serial(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Not serializable: {type(obj)}")

    row = models.SearchCache(
        query=_cache_key(query),
        results_json=json.dumps([r.model_dump() for r in results], default=_serial),
        cached_at=datetime.now(timezone.utc),
    )
    db.merge(row)
    db.commit()


@router.get("/debug")
async def scrape_debug():
    """Diagnose the scraper configuration and Worker connectivity."""
    import os, httpx
    from bs4 import BeautifulSoup
    from app.scraper import _CF_WORKER_URL, _build_url, _parse_page

    cf_url = os.environ.get("CF_WORKER_URL", "")
    result = {
        "cf_worker_url_configured": bool(cf_url),
        "cf_worker_url": cf_url or "(not set)",
    }

    if cf_url:
        test_url, extra = _build_url("mahomes prizm", 1)
        result["proxied_request_url"] = test_url
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                r = await client.get(test_url, headers=extra)
            result["worker_http_status"] = r.status_code
            result["response_bytes"] = len(r.text)

            soup = BeautifulSoup(r.text, "html.parser")
            s_items = soup.select(".s-item")
            pl_items = soup.select(".s-item__pl-on-bottom")
            result["s_item_count"] = len(s_items)
            result["s_item_pl_count"] = len(pl_items)

            # Show first real item's raw HTML to check selectors
            for item in s_items:
                title_el = item.select_one(".s-item__title")
                if title_el and "Shop on eBay" not in title_el.get_text():
                    result["first_item_html"] = str(item)[:1500]
                    result["first_item_title"] = title_el.get_text(strip=True)
                    price_el = item.select_one(".s-item__price")
                    result["first_item_price_text"] = price_el.get_text(strip=True) if price_el else None
                    break

            parsed = _parse_page(r.text)
            result["parsed_count"] = len(parsed)
            if parsed:
                result["first_parsed"] = parsed[0].model_dump()

        except Exception as e:
            result["error"] = str(e)

    return result


@router.get("/search", response_model=list[schemas.ScrapedListing])
async def search_ebay_get(
    query: str = Query(..., min_length=1, description="e.g. 'Patrick Mahomes 2017 Prizm PSA 10'"),
    max_pages: int = Query(1, ge=1, le=3),
    db: Session = Depends(get_db),
):
    """Search eBay sold listings. Returns cached results (6 h TTL) to minimise scrape calls."""
    cached = _get_cached(db, query)
    if cached:
        log.info("Cache hit for query: %s (%d results)", query, len(cached))
        return cached

    results = await scrape_sold_listings(query, max_pages)
    if not results:
        raise HTTPException(status_code=404, detail="No sold listings found. Try a broader search query.")

    _set_cache(db, query, results)
    return results


@router.post("/search", response_model=list[schemas.ScrapedListing])
async def search_ebay(payload: schemas.ScrapeSearchRequest, db: Session = Depends(get_db)):
    """Search eBay completed/sold listings without saving to the sales table."""
    cached = _get_cached(db, payload.query)
    if cached:
        return cached

    results = await scrape_sold_listings(payload.query, payload.max_pages)
    if not results:
        raise HTTPException(status_code=404, detail="No sold listings found. Try a broader search query.")

    _set_cache(db, payload.query, results)
    return results


@router.post("/import", response_model=schemas.ScrapeImportResult)
async def import_ebay_sales(
    payload: schemas.ScrapeImportRequest,
    db: Session = Depends(get_db),
):
    """
    Scrape eBay sold listings and import them as Sales for the given card_id.
    Listings already in the database (matched by listing_url) are skipped.
    """
    card = db.get(models.Card, payload.card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    listings = await scrape_sold_listings(payload.query, payload.max_pages)
    if not listings:
        raise HTTPException(
            status_code=404,
            detail="No sold listings found. Try a broader search query.",
        )

    imported_sales: list[models.Sale] = []
    skipped = 0
    errors = 0

    for listing in listings:
        if listing.sale_date is None:
            skipped += 1
            continue

        sale = models.Sale(
            card_id=payload.card_id,
            sale_price=listing.sale_price,
            sale_date=listing.sale_date,
            platform="eBay",
            condition=listing.condition,
            notes=listing.title,
            listing_url=listing.listing_url,
        )
        db.add(sale)
        try:
            db.flush()
            imported_sales.append(sale)
        except IntegrityError:
            db.rollback()
            skipped += 1
        except Exception:
            db.rollback()
            errors += 1

    db.commit()

    loaded = (
        db.query(models.Sale)
        .options(joinedload(models.Sale.card).joinedload(models.Card.player))
        .filter(models.Sale.id.in_([s.id for s in imported_sales]))
        .all()
    )

    return schemas.ScrapeImportResult(
        imported=len(imported_sales),
        skipped=skipped,
        errors=errors,
        sales=loaded,
    )
