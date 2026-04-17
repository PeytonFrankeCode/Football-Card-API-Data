from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.database import engine, Base
from app.routers import players, cards, sales, analytics, scrape

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

app.include_router(players.router)
app.include_router(cards.router)
app.include_router(sales.router)
app.include_router(analytics.router)
app.include_router(scrape.router)

# Serve the frontend — must come last so API routes take priority
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
