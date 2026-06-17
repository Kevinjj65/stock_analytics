"""
database.py — Async SQLAlchemy engine and session setup.

This file is the single source of truth for all database connections.
Every route that needs to query the database uses the get_db() dependency
defined here. SQLAlchemy talks directly to Supabase's PostgreSQL over asyncpg.

The engine is created LAZILY on the first request — not at module import time.
This allows the app to be imported and tested without a .env file present.
"""

from sqlalchemy.ext.asyncio import (
    create_async_engine,    # Creates an async-compatible database engine (uses asyncpg under the hood)
    AsyncSession,           # The async version of SQLAlchemy's Session — used for all DB queries
    async_sessionmaker      # Factory that produces new AsyncSession instances on demand
)
from sqlalchemy.orm import DeclarativeBase   # Base class that all SQLAlchemy ORM models must inherit from
from typing import AsyncGenerator, Optional  # AsyncGenerator for get_db type hint; Optional for nullable module-level vars


# ---------------------------------------------------------------------------
# ORM BASE CLASS
# All SQLAlchemy model classes in models/ inherit from Base.
# Defined first (before the engine) because models import it at their module level.
# Base tracks the table definitions so Alembic can generate migrations.
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):    # Declarative base class — models inherit from this to register with SQLAlchemy's metadata
    pass                        # No custom configuration needed — the default DeclarativeBase behaviour is sufficient


# ---------------------------------------------------------------------------
# LAZY ENGINE INITIALISATION
# The engine and session factory are module-level variables initialised to None.
# They are created on the FIRST call to get_db(), not at import time.
# This pattern prevents the engine from being built when files are imported
# during tests or when no .env file is present yet.
# ---------------------------------------------------------------------------

_engine: Optional[object] = None              # Holds the AsyncEngine once it has been created; None until first request
_AsyncSessionLocal: Optional[object] = None   # Holds the async_sessionmaker once the engine is ready; None until first request


def _get_engine():                             # Internal helper that builds (or returns) the shared async engine
    """
    Creates the SQLAlchemy async engine on first call and caches it.
    All subsequent calls return the same cached engine instance.
    The engine is never re-created during the lifetime of the process.
    """
    global _engine, _AsyncSessionLocal         # Declare we are modifying the module-level variables (not creating local ones)

    if _engine is not None:                    # If the engine was already created by a previous request, reuse it
        return _engine, _AsyncSessionLocal     # Return the cached engine and session factory — no setup work needed

    from app.core.config import settings       # Import settings here (not at top level) so import-time failures don't cascade

    _engine = create_async_engine(            # Build the async engine — this is the one-time setup
        settings.SUPABASE_DB_URL,             # PostgreSQL connection string from .env (must start with postgresql+asyncpg://)
        echo=settings.ENVIRONMENT == "development",  # Log SQL statements to console in development mode only
        pool_size=10,                         # Keep 10 persistent connections open in the pool at all times
        max_overflow=20,                      # Allow up to 20 extra temporary connections when the pool is fully used
        pool_pre_ping=True,                   # Test each connection before use — silently drops stale/broken connections
    )

    _AsyncSessionLocal = async_sessionmaker(  # Build the session factory bound to the engine we just created
        bind=_engine,                         # Every session produced by this factory connects through our engine
        class_=AsyncSession,                  # Use the async session class (not the sync Session)
        expire_on_commit=False,               # ORM objects remain accessible after commit without a new DB round-trip
        autocommit=False,                     # Transactions are managed manually — we commit or rollback explicitly
        autoflush=False,                      # Pending changes are not auto-flushed — we flush when needed
    )

    return _engine, _AsyncSessionLocal        # Return both the engine and the session factory to the caller


# ---------------------------------------------------------------------------
# FASTAPI DATABASE DEPENDENCY
# get_db() is injected into route functions via FastAPI's Depends().
# It opens a session at the start of the request and closes it at the end,
# even if an exception is raised (the try/finally guarantees cleanup).
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:   # Async generator — yields one session per request
    """
    FastAPI dependency that provides a database session to route handlers.

    Usage in a route:
        @router.get("/stocks")
        async def list_stocks(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Stock))
    """
    _, session_factory = _get_engine()                       # Ensure the engine is ready; get the session factory (creates engine on first call)

    async with session_factory() as session:                 # Open a new AsyncSession — begins a transaction automatically
        try:
            yield session                                    # Hand the session to the route function — the route's code runs here
            await session.commit()                           # If the route completed without error, commit all DB changes
        except Exception:
            await session.rollback()                         # If anything went wrong, undo all uncommitted changes
            raise                                            # Re-raise so FastAPI returns the correct HTTP error to the client
