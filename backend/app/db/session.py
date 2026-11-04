"""Database engine and session management for SQLAlchemy."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base declarative class for all application SQLAlchemy models."""
    pass


# Centralized SQLAlchemy engine instantiated once across application lifespan
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session and guarantees closure."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Context manager providing a database session for background or streaming generators."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

