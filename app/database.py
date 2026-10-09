"""Database configuration and session dependency."""

import os
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./certificates.db")


def _make_engine(url: str):
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, **kwargs)


engine = _make_engine(_database_url())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_database() -> None:
    """Create tables and add columns introduced after the initial release."""
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    migrations = {
        "generation_jobs": ("pass_mark", "FLOAT NOT NULL DEFAULT 60"),
        "certificate_results": ("score_percent", "FLOAT NOT NULL DEFAULT 100"),
    }
    with engine.begin() as connection:
        for table, (column, definition) in migrations.items():
            existing = {item["name"] for item in inspector.get_columns(table)}
            if column not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
