import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
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


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "docs": "/docs"}


# Serve the frontend only if the directory exists (local dev).
# On Render the frontend is hosted on GitHub Pages instead.
_frontend = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
)
if os.path.isdir(_frontend):
    app.mount("/ui", StaticFiles(directory=_frontend, html=True), name="frontend")
