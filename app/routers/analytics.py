from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/price-summary", response_model=list[schemas.PriceSummary])
def price_summary(
    player_id: int | None = Query(None),
    is_rookie: bool | None = Query(None),
    is_autograph: bool | None = Query(None),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
):
    """Return avg/min/max sale price grouped by card."""
    last_sale_subq = (
        db.query(
            models.Sale.card_id,
            models.Sale.sale_price.label("last_price"),
            models.Sale.sale_date.label("last_date"),
        )
        .distinct(models.Sale.card_id)
        .order_by(models.Sale.card_id, models.Sale.sale_date.desc())
        .subquery()
    )

    rows = (
        db.query(
            models.Card.id,
            models.Player.name,
            models.Card.year,
            models.Card.brand,
            models.Card.variant,
            func.count(models.Sale.id).label("sale_count"),
            func.avg(models.Sale.sale_price).label("avg_price"),
            func.min(models.Sale.sale_price).label("min_price"),
            func.max(models.Sale.sale_price).label("max_price"),
            last_sale_subq.c.last_price,
            last_sale_subq.c.last_date,
        )
        .join(models.Player, models.Card.player_id == models.Player.id)
        .join(models.Sale, models.Sale.card_id == models.Card.id)
        .join(last_sale_subq, last_sale_subq.c.card_id == models.Card.id)
        .group_by(models.Card.id, models.Player.name, last_sale_subq.c.last_price, last_sale_subq.c.last_date)
    )

    if player_id is not None:
        rows = rows.filter(models.Card.player_id == player_id)
    if is_rookie is not None:
        rows = rows.filter(models.Card.is_rookie == is_rookie)
    if is_autograph is not None:
        rows = rows.filter(models.Card.is_autograph == is_autograph)

    rows = rows.order_by(func.avg(models.Sale.sale_price).desc()).limit(limit).all()

    return [
        schemas.PriceSummary(
            card_id=r[0],
            player_name=r[1],
            year=r[2],
            brand=r[3],
            variant=r[4],
            sale_count=r[5],
            avg_price=round(r[6], 2),
            min_price=r[7],
            max_price=r[8],
            last_sale_price=r[9],
            last_sale_date=r[10],
        )
        for r in rows
    ]


@router.get("/top-sales", response_model=list[schemas.TopSale])
def top_sales(
    limit: int = Query(10, le=100),
    player_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """Return the highest individual sale prices."""
    q = (
        db.query(
            models.Sale.id,
            models.Player.name,
            models.Card.year,
            models.Card.brand,
            models.Card.variant,
            models.Sale.sale_price,
            models.Sale.sale_date,
            models.Sale.platform,
        )
        .join(models.Card, models.Sale.card_id == models.Card.id)
        .join(models.Player, models.Card.player_id == models.Player.id)
    )
    if player_id is not None:
        q = q.filter(models.Card.player_id == player_id)

    rows = q.order_by(models.Sale.sale_price.desc()).limit(limit).all()

    return [
        schemas.TopSale(
            sale_id=r[0],
            player_name=r[1],
            year=r[2],
            brand=r[3],
            variant=r[4],
            sale_price=r[5],
            sale_date=r[6],
            platform=r[7],
        )
        for r in rows
    ]
