import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=schemas.RegisterResponse)
def register(payload: schemas.RegisterRequest, db: Session = Depends(get_db)):
    """Sign up with your email to receive an API key."""
    email = payload.email.lower().strip()

    existing = db.query(models.ApiKey).filter(models.ApiKey.email == email).first()
    if existing:
        return schemas.RegisterResponse(
            email=existing.email,
            api_key=existing.key,
            message="You already have an API key — here it is again.",
        )

    key = "gc_" + secrets.token_urlsafe(32)
    record = models.ApiKey(email=email, key=key)
    db.add(record)
    db.commit()
    db.refresh(record)

    return schemas.RegisterResponse(
        email=record.email,
        api_key=record.key,
        message="API key created. Include it as the X-API-Key header in your requests.",
    )


@router.get("/me", response_model=schemas.ApiKeyInfo)
def get_me(api_key: str, db: Session = Depends(get_db)):
    """Look up info and usage stats for your API key."""
    record = db.query(models.ApiKey).filter(models.ApiKey.key == api_key).first()
    if not record or not record.is_active:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key.")
    return record
