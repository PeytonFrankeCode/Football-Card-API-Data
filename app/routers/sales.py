from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/sales", tags=["Sales"])


def _load_sale(sale_id: int, db: Session) -> models.Sale:
    sale = (
        db.query(models.Sale)
        .options(
            joinedload(models.Sale.card).joinedload(models.Card.player)
        )
        .filter(models.Sale.id == sale_id)
        .first()
    )
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    return sale


@router.get("/", response_model=list[schemas.SaleOut])
def list_sales(
    card_id: int | None = Query(None),
    player_id: int | None = Query(None),
    platform: str | None = Query(None, description="Partial match"),
    condition: str | None = Query(None),
    min_price: int | None = Query(None, gt=0, description="Minimum price in integer cents"),
    max_price: int | None = Query(None, gt=0, description="Maximum price in integer cents"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    skip: int = 0,
    limit: int = Query(50, le=1000),
    db: Session = Depends(get_db),
):
    q = (
        db.query(models.Sale)
        .options(joinedload(models.Sale.card).joinedload(models.Card.player))
        .join(models.Card)
    )
    if card_id is not None:
        q = q.filter(models.Sale.card_id == card_id)
    if player_id is not None:
        q = q.filter(models.Card.player_id == player_id)
    if platform:
        q = q.filter(models.Sale.platform.ilike(f"%{platform}%"))
    if condition:
        q = q.filter(models.Sale.condition.ilike(f"%{condition}%"))
    if min_price is not None:
        q = q.filter(models.Sale.sale_price >= min_price / 100.0)
    if max_price is not None:
        q = q.filter(models.Sale.sale_price <= max_price / 100.0)
    if date_from:
        q = q.filter(models.Sale.sale_date >= date_from)
    if date_to:
        q = q.filter(models.Sale.sale_date <= date_to)
    return q.order_by(models.Sale.sale_date.desc()).offset(skip).limit(limit).all()


@router.get("/{sale_id}", response_model=schemas.SaleOut)
def get_sale(sale_id: int, db: Session = Depends(get_db)):
    return _load_sale(sale_id, db)


@router.post("/", response_model=schemas.SaleOut, status_code=201)
def create_sale(payload: schemas.SaleCreate, db: Session = Depends(get_db)):
    if not db.get(models.Card, payload.card_id):
        raise HTTPException(status_code=404, detail="Card not found")
    data = payload.model_dump()
    data["sale_price"] = data["sale_price"] / 100.0  # cents -> dollars for storage
    sale = models.Sale(**data)
    db.add(sale)
    db.commit()
    db.refresh(sale)
    return _load_sale(sale.id, db)


@router.patch("/{sale_id}", response_model=schemas.SaleOut)
def update_sale(sale_id: int, payload: schemas.SaleUpdate, db: Session = Depends(get_db)):
    sale = db.get(models.Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    updates = payload.model_dump(exclude_unset=True)
    if "card_id" in updates and not db.get(models.Card, updates["card_id"]):
        raise HTTPException(status_code=404, detail="Card not found")
    if updates.get("sale_price") is not None:
        updates["sale_price"] = updates["sale_price"] / 100.0  # cents -> dollars
    for field, value in updates.items():
        setattr(sale, field, value)
    db.commit()
    return _load_sale(sale_id, db)


@router.delete("/{sale_id}", status_code=204)
def delete_sale(sale_id: int, db: Session = Depends(get_db)):
    sale = db.get(models.Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    db.delete(sale)
    db.commit()
