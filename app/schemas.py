from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, field_serializer


def _to_cents(value: float | None) -> int | None:
    """Convert a stored dollar amount to integer cents for the API wire format.

    PriceCharting-style contract: prices travel as an integer number of pennies
    (e.g. $15.99 -> 1599), which avoids floating-point rounding on the wire.
    Applied only on JSON serialization, so internally stored values and the
    scrape cache keep their original dollar amounts.
    """
    if value is None:
        return None
    return int(round(float(value) * 100))


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
    grade_company: str | None = Field(None, max_length=20, description="PSA, BGS, SGC, CGC, …")
    seller: str | None = Field(None, max_length=200)
    item_number: str | None = Field(None, max_length=50, description="Stable marketplace listing id")
    notes: str | None = None
    listing_url: str | None = None


class SaleCreate(SaleBase):
    # Prices are accepted as integer cents (e.g. 1599 = $15.99).
    sale_price: int = Field(..., gt=0, description="Sale price in integer cents, e.g. 1599 = $15.99")


class SaleUpdate(BaseModel):
    card_id: int | None = None
    sale_price: int | None = Field(None, gt=0, description="Sale price in integer cents, e.g. 1599 = $15.99")
    sale_date: datetime | None = None
    platform: str | None = None
    condition: str | None = None
    grade: float | None = Field(None, ge=1.0, le=10.0)
    grade_company: str | None = Field(None, max_length=20)
    seller: str | None = Field(None, max_length=200)
    item_number: str | None = Field(None, max_length=50)
    notes: str | None = None
    listing_url: str | None = None


class SaleOut(SaleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    card: CardOut

    @field_serializer("sale_price", when_used="json")
    def _ser_sale_price(self, v: float) -> int | None:
        return _to_cents(v)


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

    @field_serializer("avg_price", "min_price", "max_price", "last_sale_price", when_used="json")
    def _ser_prices(self, v: float) -> int | None:
        return _to_cents(v)


class TopSale(BaseModel):
    sale_id: int
    player_name: str
    year: int
    brand: str
    variant: str | None
    sale_price: float
    sale_date: datetime
    platform: str | None

    @field_serializer("sale_price", when_used="json")
    def _ser_sale_price(self, v: float) -> int | None:
        return _to_cents(v)


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
    image_url: str | None = None
    # Stable eBay item number parsed from the listing URL (e.g. /itm/123456789).
    # Used to de-duplicate the same listing appearing across pages.
    item_number: str | None = None

    @field_serializer("sale_price", when_used="json")
    def _ser_sale_price(self, v: float) -> int | None:
        return _to_cents(v)


class BrowseListing(BaseModel):
    """An ACTIVE listing from the official eBay Browse API."""
    item_id: str
    title: str
    price: float | None = None
    currency: str | None = None
    condition: str | None = None
    image_url: str | None = None
    item_web_url: str | None = None
    seller: str | None = None
    item_location: str | None = None

    @field_serializer("price", when_used="json")
    def _ser_price(self, v: float | None) -> int | None:
        return _to_cents(v)


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
