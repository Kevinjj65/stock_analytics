"""
config.py — Application settings loader.

Pydantic-Settings reads values from the .env file automatically.
Every environment variable we need is declared as a typed field here.
Any missing required variable will raise a clear error on startup.
"""

from pydantic_settings import BaseSettings  # BaseSettings is pydantic-settings' class that reads from .env files
from pydantic import Field                  # Field lets us add default values and descriptions to each setting
from functools import lru_cache             # lru_cache ensures we only parse the .env file once, not on every request


class Settings(BaseSettings):
    """
    Central settings object for the entire application.
    All values are loaded from the .env file in the backend directory.
    """

    # --- Supabase connection details ---
    SUPABASE_URL: str = Field(                          # The full Supabase project URL (e.g. https://xyz.supabase.co)
        ...,                                            # '...' means this field is required — app will crash if missing
        description="Supabase project URL"              # Human-readable description shown in docs/errors
    )

    SUPABASE_KEY: str = Field(                          # The Supabase anon/service role API key for REST API calls
        ...,                                            # Required — no default value allowed
        description="Supabase API key (anon or service role)"
    )

    SUPABASE_DB_URL: str = Field(                       # The raw PostgreSQL connection string for SQLAlchemy
        ...,                                            # Required — format: postgresql+asyncpg://user:pass@host:port/db
        description="Direct PostgreSQL connection string for SQLAlchemy"
    )

    # --- JWT authentication settings ---
    JWT_SECRET_KEY: str = Field(                        # Secret key used to sign and verify JWT tokens — must be kept private
        ...,                                            # Required — generate a strong random string for production
        description="Secret key for signing JWT tokens"
    )

    JWT_ALGORITHM: str = Field(                         # Hashing algorithm used for JWT signing (HS256 = HMAC + SHA-256)
        default="HS256",                                # HS256 is the standard default for symmetric JWT signing
        description="JWT signing algorithm"
    )

    JWT_EXPIRE_MINUTES: int = Field(                    # How many minutes a JWT access token stays valid before expiring
        default=30,                                     # 30 minutes is a secure default; refresh tokens handle longer sessions
        description="JWT token expiry time in minutes"
    )

    # --- Application environment ---
    ENVIRONMENT: str = Field(                           # Indicates which environment the app is running in
        default="development",                          # Defaults to development so local runs work without explicit setting
        description="Runtime environment: development, staging, or production"
    )

    class Config:
        """
        Pydantic-Settings configuration block.
        Tells pydantic where to look for environment variable values.
        """
        env_file = ".env"           # Load values from a file named .env in the working directory (backend/)
        env_file_encoding = "utf-8" # Treat the .env file as UTF-8 encoded text
        case_sensitive = True       # Variable names must match exactly (SUPABASE_URL != supabase_url)


@lru_cache()                        # Cache the Settings instance so .env is only parsed once per process lifetime
def get_settings() -> Settings:     # Returns the single shared Settings instance used throughout the application
    """
    Returns the cached Settings instance.
    Import and call this function wherever you need a config value.
    Example: settings = get_settings(); print(settings.ENVIRONMENT)
    """
    return Settings()               # Instantiate Settings — pydantic reads .env and validates all fields here


# Create one module-level instance so other modules can do: from app.core.config import settings
settings = get_settings()           # This is the primary import used across the application (e.g. in database.py, main.py)
