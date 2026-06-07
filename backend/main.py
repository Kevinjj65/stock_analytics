"""
main.py — FastAPI application entry point.

This is the file uvicorn runs to start the backend server:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

All routers (stocks, analytics, auth, etc.) will be registered here as the project grows.
"""

from fastapi import FastAPI                           # FastAPI is the web framework — creates the app and handles routing
from fastapi.middleware.cors import CORSMiddleware    # CORS middleware allows the React frontend to call this API from a different port/domain
from contextlib import asynccontextmanager            # asynccontextmanager lets us define async startup and shutdown logic cleanly
from app.core.config import settings                  # Import the shared settings object to read ENVIRONMENT and other config values


# ---------------------------------------------------------------------------
# LIFESPAN: startup and shutdown logic
# The lifespan function runs setup code before the app starts accepting requests,
# and teardown code after the server shuts down. This replaces the old @app.on_event pattern.
# ---------------------------------------------------------------------------

@asynccontextmanager                              # Decorator that turns this async function into a lifespan context manager
async def lifespan(app: FastAPI):                 # FastAPI passes the app instance so we can access it during startup/shutdown
    """
    Code before 'yield' runs on startup.
    Code after 'yield' runs on shutdown.
    """
    # --- STARTUP ---
    print(f"Starting Stock Analytics API in [{settings.ENVIRONMENT}] mode")  # Log which environment we are running in
    yield                                         # Hand control back to FastAPI — server is now running and accepting requests

    # --- SHUTDOWN ---
    print("Shutting down Stock Analytics API")   # Log that the server is stopping (useful for debugging container restarts)


# ---------------------------------------------------------------------------
# APP INSTANCE
# This is the central FastAPI object — everything registers to it.
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Stock Market Analytics API",          # Name shown in the auto-generated Swagger UI at /docs
    description=(                                # Description shown in Swagger UI — explains what this API does
        "REST API for the Stock Market Analytics Platform. "
        "Provides stock prices, technical indicators, sentiment analysis, "
        "anomaly detection, price forecasting, clustering, and portfolio management."
    ),
    version="1.0.0",                             # API version shown in Swagger UI — update this as the API evolves
    docs_url="/docs",                            # URL where Swagger UI is served (interactive API documentation)
    redoc_url="/redoc",                          # URL where ReDoc UI is served (alternative API documentation format)
    lifespan=lifespan,                           # Register the startup/shutdown handler we defined above
)


# ---------------------------------------------------------------------------
# CORS MIDDLEWARE
# Cross-Origin Resource Sharing must be configured so the React frontend
# (running on localhost:3000 or Vercel) can call this API without the browser blocking it.
# In production, replace "*" with the actual frontend domain.
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,                              # Register FastAPI's built-in CORS middleware
    allow_origins=[                              # List of domains allowed to make requests to this API
        "http://localhost:3000",                 # React dev server (local development)
        "http://localhost:5173",                 # Vite dev server (if using Vite instead of CRA)
        "https://*.vercel.app",                  # Any Vercel preview deployment URL
    ] if settings.ENVIRONMENT != "development" else ["*"],  # In development, allow all origins for convenience
    allow_credentials=True,                      # Allow cookies and Authorization headers to be sent cross-origin
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # HTTP methods the frontend is allowed to use
    allow_headers=["*"],                         # Allow all request headers (includes Authorization for JWT tokens)
)


# ---------------------------------------------------------------------------
# HEALTH CHECK ENDPOINT
# A lightweight endpoint that confirms the API is running.
# Used by UptimeRobot to monitor uptime, and by Docker health checks.
# Returns immediately without hitting the database so it always responds fast.
# ---------------------------------------------------------------------------

@app.get(                                        # Register a GET route on the path below
    "/health",                                   # The URL path for this endpoint: GET /health
    tags=["Health"],                             # Groups this endpoint under 'Health' in Swagger UI
    summary="Check API health",                  # Short description shown in Swagger UI
)
async def health_check():                        # Async function that handles GET /health requests
    """
    Returns a simple JSON response confirming the API is alive.
    This endpoint must remain fast and never depend on the database.
    """
    return {                                     # Return a JSON object — FastAPI serialises this dict automatically
        "status": "ok",                          # Fixed string — monitoring tools check for this value
        "environment": settings.ENVIRONMENT,     # Shows which environment is running (development / production)
        "version": app.version,                  # Returns the API version string defined above ("1.0.0")
    }


# ---------------------------------------------------------------------------
# ROOT ENDPOINT
# A friendly landing response at the base URL so the API isn't a blank page.
# ---------------------------------------------------------------------------

@app.get(                                        # Register a GET route at the root path
    "/",                                         # The URL path: GET /
    tags=["Root"],                               # Groups this in Swagger UI under 'Root'
    summary="API root",
)
async def root():                                # Async function that handles GET / requests
    """Returns a welcome message at the API root."""
    return {                                     # Return a simple JSON welcome response
        "message": "Stock Market Analytics API",  # Human-readable name of this API
        "docs": "/docs",                         # Tells the caller where to find the Swagger documentation
    }
