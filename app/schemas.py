from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


# ── Player ────────────────────────────────────────────────────────────────────

class PlayerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    team: str | None = None
    position: str | None = None


class PlayerCreate(PlayerBase):
    pass


class PlayerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    team: str | None = None
    position: str | None = None


class PlayerOut(PlayerBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ── Card ──────────────────────────────────────────────────────────────────────

class CardBase(BaseModel):
    player_id: int
    year: int = Field(..., ge=1950, le=2100)
    brand: str = Field(..., min_length=1, max_length=100)
    set_name: str | None = None
    card_number: str | None = None
    variant: str | None = None
    is_rookie: bool = False
    is_autograph: bool = False
    is_patch: bool = False
    print_run: int | None = Field(None, gt=0)


class CardCreate(CardBase):
    pass


class CardUpdate(BaseModel):
    player_id: int | None = None
    year: int | None = Field(None, ge=1950, le=2100)
    brand: str | None = Field(None, min_length=1, max_length=100)
    set_name: str | None = None
    card_number: str | None = None
    variant: str | None = None
    is_rookie: bool | None = None
    is_autograph: bool | None = None
    is_patch: bool | None = None
    print_run: int | None = Field(None, gt=0)


class CardOut(CardBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    player: PlayerOut


# ── Sale ──────────────────────────────────────────────────────────────────────

class SaleBase(BaseModel):
    card_id: int
    sale_price: float = Field(..., gt=0)
    sale_date: datetime
    platform: str | None = None
    condition: str | None = None
    grade: float | None = Field(None, ge=1.0, le=10.0)
    notes: str | None = None
    listing_url: str | None = None


class SaleCreate(SaleBase):
    pass


class SaleUpdate(BaseModel):
    card_id: int | None = None
    sale_price: float | None = Field(None, gt=0)
    sale_date: datetime | None = None
    platform: str | None = None
    condition: str | None = None
    grade: float | None = Field(None, ge=1.0, le=10.0)
    notes: str | None = None
    listing_url: str | None = None


class SaleOut(SaleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    card: CardOut


# ── Analytics ─────────────────────────────────────────────────────────────────

class PriceSummary(BaseModel):
    card_id: int
    player_name: str
    year: int
    brand: str
    variant: str | None
    sale_count: int
    avg_price: float
    min_price: float
    max_price: float
    last_sale_price: float
    last_sale_date: datetime


class TopSale(BaseModel):
    sale_id: int
    player_name: str
    year: int
    brand: str
    variant: str | None
    sale_price: float
    sale_date: datetime
    platform: str | None


# ── Auth / API Keys ───────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=200, description="Your email address")

class RegisterResponse(BaseModel):
    email: str
    api_key: str
    message: str

class ApiKeyInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    email: str
    is_active: bool
    request_count: int
    created_at: datetime


# ── Scrape ────────────────────────────────────────────────────────────────────

class ScrapedListing(BaseModel):
    title: str
    sale_price: float
    sale_date: datetime | None
    condition: str | None
    listing_url: str


class ScrapeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="eBay search query, e.g. 'Patrick Mahomes 2017 Prizm PSA 10'")
    max_pages: int = Field(1, ge=1, le=5)


class ScrapeImportRequest(BaseModel):
    card_id: int
    query: str = Field(..., min_length=1, description="eBay search query for this card")
    max_pages: int = Field(1, ge=1, le=5)


class ScrapeImportResult(BaseModel):
    imported: int
    skipped: int
    errors: int
    sales: list[SaleOut]
