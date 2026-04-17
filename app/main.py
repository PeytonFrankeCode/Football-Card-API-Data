from fastapi import FastAPI
from app.database import engine, Base
from app.routers import players, cards, sales, analytics, scrape

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Football Card Sales API",
    description=(
        "Track and query sold data for American football cards. "
        "Supports players, card listings, individual sales, and price analytics."
    ),
    version="1.0.0",
)

app.include_router(players.router)
app.include_router(cards.router)
app.include_router(sales.router)
app.include_router(analytics.router)
app.include_router(scrape.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "docs": "/docs"}
