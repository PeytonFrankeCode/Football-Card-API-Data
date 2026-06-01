from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )



class SearchCache(Base):
    __tablename__ = "search_cache"

    query: Mapped[str] = mapped_column(String(500), primary_key=True)
    results_json: Mapped[str] = mapped_column(Text, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    team: Mapped[str | None] = mapped_column(String(100))
    position: Mapped[str | None] = mapped_column(String(10))

    cards: Mapped[list["Card"]] = relationship("Card", back_populates="player")


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    set_name: Mapped[str | None] = mapped_column(String(100))
    card_number: Mapped[str | None] = mapped_column(String(50))
    variant: Mapped[str | None] = mapped_column(String(100))
    # Card attributes
    is_rookie: Mapped[bool] = mapped_column(Boolean, default=False)
    is_autograph: Mapped[bool] = mapped_column(Boolean, default=False)
    is_patch: Mapped[bool] = mapped_column(Boolean, default=False)
    print_run: Mapped[int | None] = mapped_column(Integer)  # e.g. 99 means /99

    player: Mapped["Player"] = relationship("Player", back_populates="cards")
    sales: Mapped[list["Sale"]] = relationship("Sale", back_populates="card")


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), nullable=False, index=True)
    sale_price: Mapped[float] = mapped_column(Float, nullable=False)
    sale_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    platform: Mapped[str | None] = mapped_column(String(50))  # e.g. eBay, COMC, StockX
    condition: Mapped[str | None] = mapped_column(String(20))  # Raw, PSA, BGS, SGC
    grade: Mapped[float | None] = mapped_column(Float)         # e.g. 9.5, 10
    grade_company: Mapped[str | None] = mapped_column(String(20))   # PSA, BGS, SGC, CGC
    seller: Mapped[str | None] = mapped_column(String(200))         # marketplace seller handle
    item_number: Mapped[str | None] = mapped_column(String(50), index=True)  # stable listing id
    notes: Mapped[str | None] = mapped_column(Text)
    listing_url: Mapped[str | None] = mapped_column(String(500), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    card: Mapped["Card"] = relationship("Card", back_populates="sales")
