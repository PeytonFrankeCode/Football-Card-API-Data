import asyncio
import logging
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logging.basicConfig(level=logging.INFO)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.database import engine, Base, run_migrations
from app.routers import players, cards, sales, analytics, scrape, auth

Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(
    title="GridironCards API",
    description=(
        "Track and query sold data for American football cards. "
        "Supports players, card listings, individual sales, eBay scraping, and price analytics.\n\n"
        "**Conventions:** prices are integer cents (e.g. 1599 = $15.99); dates are ISO 8601; "
        "errors return `{\"status\": \"error\", \"error_message\": ...}`."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── Consistent error envelope (PriceCharting-style) ───────────────────────────
# Every error response is { "status": "error", "error_message": <human text> }.

@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "error_message": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"status": "error", "error_message": "Validation failed", "errors": exc.errors()},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(players.router)
app.include_router(cards.router)
app.include_router(sales.router)
app.include_router(analytics.router)
app.include_router(scrape.router)


@app.get("/health")
def health():
    return {"status": "ok"}


async def _keep_alive():
    base = os.environ.get("RENDER_EXTERNAL_URL", "https://api.thecardhuddle.com")
    url = base.rstrip("/") + "/health"
    await asyncio.sleep(60)  # wait for full startup before first ping
    while True:
        try:
            async with httpx.AsyncClient() as client:
                await client.get(url, timeout=10)
        except Exception:
            pass
        await asyncio.sleep(10 * 60)  # ping every 10 minutes


@app.on_event("startup")
async def startup():
    asyncio.create_task(_keep_alive())


# Serve the frontend only if the directory exists (local dev + Render).
_frontend = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
)
if os.path.isdir(_frontend):
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")

