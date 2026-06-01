import logging
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./football_cards.db")

# Render's Postgres URLs start with postgres://, SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _sqlite else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added after the initial schema. SQLAlchemy's create_all() only creates
# missing *tables*, never missing *columns*, so we additively patch existing
# tables on startup. Works on both SQLite and Postgres (plain ADD COLUMN).
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "sales": {
        "grade_company": "VARCHAR(20)",
        "seller": "VARCHAR(200)",
        "item_number": "VARCHAR(50)",
    },
}


def run_migrations() -> None:
    """Idempotently add any columns missing from existing tables."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue  # create_all() will build it fresh with all columns
            present = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl_type in columns.items():
                if name in present:
                    continue
                log.info("Adding missing column %s.%s", table, name)
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl_type}'))
