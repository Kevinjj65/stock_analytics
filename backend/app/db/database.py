"""
database.py — Async SQLAlchemy engine and session setup.

This file is the single source of truth for all database connections.
Every route that needs to query the database uses the get_db() dependency
defined here. SQLAlchemy talks directly to Supabase's PostgreSQL over asyncpg.
"""

from sqlalchemy.ext.asyncio import (
    create_async_engine,    # Creates an async-compatible database engine (uses asyncpg under the hood)
    AsyncSession,           # The async version of SQLAlchemy's Session — used for all DB queries
    async_sessionmaker      # Factory that produces new AsyncSession instances on demand
)
from sqlalchemy.orm import DeclarativeBase   # Base class that all SQLAlchemy ORM models must inherit from
from typing import AsyncGenerator            # Type hint for async generator functions (used in get_db)
from app.core.config import settings         # Import the shared settings object to get SUPABASE_DB_URL


# ---------------------------------------------------------------------------
# DATABASE ENGINE
# The engine manages the connection pool to Supabase PostgreSQL.
# asyncpg is the async PostgreSQL driver that SQLAlchemy uses here.
# ---------------------------------------------------------------------------

engine = create_async_engine(
    settings.SUPABASE_DB_URL,   # The PostgreSQL connection string from .env (must start with postgresql+asyncpg://)
    echo=settings.ENVIRONMENT == "development",  # Print all SQL statements to console only in development mode for debugging
    pool_size=10,               # Keep up to 10 persistent database connections open in the pool at all times
    max_overflow=20,            # Allow up to 20 extra temporary connections when the pool is fully used
    pool_pre_ping=True,         # Test each connection before using it — silently drops stale/dead connections
)


# ---------------------------------------------------------------------------
# SESSION FACTORY
# AsyncSessionLocal is a callable that produces one AsyncSession per request.
# Each request gets its own session so database state is never shared between requests.
# ---------------------------------------------------------------------------

AsyncSessionLocal = async_sessionmaker(
    bind=engine,                # Bind every session produced by this factory to our engine
    class_=AsyncSession,        # Use AsyncSession (not the regular sync Session)
    expire_on_commit=False,     # Keep ORM objects accessible after commit without re-querying the database
    autocommit=False,           # Do NOT auto-commit — we control transactions manually (commit/rollback in routes)
    autoflush=False,            # Do NOT auto-flush pending changes — we flush explicitly when needed
)


# ---------------------------------------------------------------------------
# ORM BASE CLASS
# All SQLAlchemy model classes in models/ inherit from Base.
# Base tracks the table definitions so Alembic can generate migrations.
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):    # Declarative base class — models inherit from this to register with SQLAlchemy's metadata
    pass                        # No custom configuration needed — the default DeclarativeBase behaviour is sufficient


# ---------------------------------------------------------------------------
# FASTAPI DATABASE DEPENDENCY
# get_db() is injected into route functions via FastAPI's Depends().
# It opens a session at the start of the request and closes it at the end,
# even if an exception is raised (the try/finally guarantees cleanup).
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:  # Returns an async generator that yields one session per call
    """
    FastAPI dependency that provides a database session to route handlers.

    Usage in a route:
        @router.get("/stocks")
        async def list_stocks(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Stock))
    """
    async with AsyncSessionLocal() as session:  # Open a new AsyncSession — this also begins a transaction automatically
        try:
            yield session                       # Hand the session to the route function — route runs here
            await session.commit()              # If the route completed without error, commit all changes to the database
        except Exception:
            await session.rollback()            # If anything went wrong, undo all changes made during this request
            raise                               # Re-raise the original exception so FastAPI can return the correct error response
