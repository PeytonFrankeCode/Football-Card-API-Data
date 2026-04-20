from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/cards", tags=["Cards"])


@router.get("/", response_model=list[schemas.CardOut])
def list_cards(
    player_id: int | None = Query(None),
    year: int | None = Query(None),
    brand: str | None = Query(None, description="Partial match"),
    is_rookie: bool | None = Query(None),
    is_autograph: bool | None = Query(None),
    is_patch: bool | None = Query(None),
    skip: int = 0,
    limit: int = Query(50, le=1000),
    db: Session = Depends(get_db),
):
    q = db.query(models.Card).options(joinedload(models.Card.player))
    if player_id is not None:
        q = q.filter(models.Card.player_id == player_id)
    if year is not None:
        q = q.filter(models.Card.year == year)
    if brand:
        q = q.filter(models.Card.brand.ilike(f"%{brand}%"))
    if is_rookie is not None:
        q = q.filter(models.Card.is_rookie == is_rookie)
    if is_autograph is not None:
        q = q.filter(models.Card.is_autograph == is_autograph)
    if is_patch is not None:
        q = q.filter(models.Card.is_patch == is_patch)
    return q.offset(skip).limit(limit).all()


@router.get("/{card_id}", response_model=schemas.CardOut)
def get_card(card_id: int, db: Session = Depends(get_db)):
    card = (
        db.query(models.Card)
        .options(joinedload(models.Card.player))
        .filter(models.Card.id == card_id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.post("/", response_model=schemas.CardOut, status_code=201)
def create_card(payload: schemas.CardCreate, db: Session = Depends(get_db)):
    if not db.get(models.Player, payload.player_id):
        raise HTTPException(status_code=404, detail="Player not found")
    card = models.Card(**payload.model_dump())
    db.add(card)
    db.commit()
    db.refresh(card)
    return get_card(card.id, db)


@router.patch("/{card_id}", response_model=schemas.CardOut)
def update_card(card_id: int, payload: schemas.CardUpdate, db: Session = Depends(get_db)):
    card = db.get(models.Card, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    updates = payload.model_dump(exclude_unset=True)
    if "player_id" in updates and not db.get(models.Player, updates["player_id"]):
        raise HTTPException(status_code=404, detail="Player not found")
    for field, value in updates.items():
        setattr(card, field, value)
    db.commit()
    return get_card(card_id, db)


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: int, db: Session = Depends(get_db)):
    card = db.get(models.Card, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    db.delete(card)
    db.commit()
