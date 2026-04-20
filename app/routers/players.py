from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/players", tags=["Players"])


@router.get("/", response_model=list[schemas.PlayerOut])
def list_players(
    name: str | None = Query(None, description="Filter by name (partial match)"),
    team: str | None = Query(None, description="Filter by team"),
    position: str | None = Query(None, description="Filter by position"),
    skip: int = 0,
    limit: int = Query(50, le=1000),
    db: Session = Depends(get_db),
):
    q = db.query(models.Player)
    if name:
        q = q.filter(models.Player.name.ilike(f"%{name}%"))
    if team:
        q = q.filter(models.Player.team.ilike(f"%{team}%"))
    if position:
        q = q.filter(models.Player.position.ilike(f"%{position}%"))
    return q.offset(skip).limit(limit).all()


@router.get("/{player_id}", response_model=schemas.PlayerOut)
def get_player(player_id: int, db: Session = Depends(get_db)):
    player = db.get(models.Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@router.post("/", response_model=schemas.PlayerOut, status_code=201)
def create_player(payload: schemas.PlayerCreate, db: Session = Depends(get_db)):
    player = models.Player(**payload.model_dump())
    db.add(player)
    db.commit()
    db.refresh(player)
    return player


@router.patch("/{player_id}", response_model=schemas.PlayerOut)
def update_player(player_id: int, payload: schemas.PlayerUpdate, db: Session = Depends(get_db)):
    player = db.get(models.Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(player, field, value)
    db.commit()
    db.refresh(player)
    return player


@router.delete("/{player_id}", status_code=204)
def delete_player(player_id: int, db: Session = Depends(get_db)):
    player = db.get(models.Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    db.delete(player)
    db.commit()
