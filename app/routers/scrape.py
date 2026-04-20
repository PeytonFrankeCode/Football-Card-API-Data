from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app import models, schemas
from app.scraper import scrape_sold_listings

router = APIRouter(prefix="/scrape", tags=["Scrape"])


@router.get("/search", response_model=list[schemas.ScrapedListing])
async def search_ebay_get(
    query: str = Query(..., min_length=1, description="e.g. 'Patrick Mahomes 2017 Prizm PSA 10'"),
    max_pages: int = Query(1, ge=1, le=3),
):
    """Search eBay sold listings directly — used by the website search bar."""
    results = await scrape_sold_listings(query, max_pages)
    if not results:
        raise HTTPException(
            status_code=404,
            detail="No sold listings found. Try a broader search query.",
        )
    return results


@router.post("/search", response_model=list[schemas.ScrapedListing])
async def search_ebay(payload: schemas.ScrapeSearchRequest):
    """
    Search eBay completed/sold listings and return raw results without saving.
    Useful for previewing data before deciding which card_id to attach them to.
    """
    results = await scrape_sold_listings(payload.query, payload.max_pages)
    if not results:
        raise HTTPException(
            status_code=404,
            detail="No sold listings found. Try a broader search query.",
        )
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
            db.flush()  # catch unique constraint violation per row
            imported_sales.append(sale)
        except IntegrityError:
            db.rollback()
            skipped += 1
        except Exception:
            db.rollback()
            errors += 1

    db.commit()

    # Reload with relationships for the response
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
