import asyncio
import logging
import os

import httpx
from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.database import engine, Base
from app.routers import players, cards, sales, analytics, scrape, auth

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="GridironCards API",
    description=(
        "Track and query sold data for American football cards. "
        "Supports players, card listings, individual sales, eBay scraping, and price analytics."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
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

