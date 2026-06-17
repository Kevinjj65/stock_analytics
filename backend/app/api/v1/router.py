"""
router.py — Version 1 API router.

This is the central router for all /api/v1/ endpoints.
Each feature area (stocks, analytics, auth, portfolio, etc.) has its own
endpoints file. This file imports each one and registers it here with its prefix and tag.

To add a new feature (e.g. analytics):
    1. Create backend/app/api/v1/endpoints/analytics.py with its own router
    2. Import it below and add an include_router() call
"""

from fastapi import APIRouter                                  # APIRouter groups related routes together under a shared prefix

from app.api.v1.endpoints import stocks                        # Import the stocks endpoints module which contains the stocks router
from app.api.v1.endpoints import analytics                     # Import the analytics endpoints module which contains the analytics router
from app.api.v1.endpoints import auth                          # Import the auth endpoints module which contains the auth router


# ---------------------------------------------------------------------------
# V1 ROUTER
# All routes added here are automatically prefixed with /api/v1 by main.py.
# For example, stocks routes registered with prefix="/stocks" become /api/v1/stocks/...
# ---------------------------------------------------------------------------

api_router = APIRouter()                                       # Create the top-level v1 router that main.py imports and registers


# ---------------------------------------------------------------------------
# REGISTER STOCKS ROUTES
# All routes defined in endpoints/stocks.py are included here.
# prefix="/stocks" means GET /stocks/ becomes GET /api/v1/stocks/ in the final URL.
# tags=["Stocks"] groups all these endpoints together in Swagger UI.
# ---------------------------------------------------------------------------

api_router.include_router(                                     # Register the stocks router as a sub-router of the v1 router
    stocks.router,                                             # The router object defined at the top of endpoints/stocks.py
    prefix="/stocks",                                          # All stocks routes are served under /api/v1/stocks/
    tags=["Stocks"],                                           # Groups all stock endpoints under the 'Stocks' section in Swagger UI at /docs
)


# ---------------------------------------------------------------------------
# REGISTER ANALYTICS ROUTES
# All routes defined in endpoints/analytics.py are included here.
# prefix="/analytics" means routes become /api/v1/analytics/...
# ---------------------------------------------------------------------------

api_router.include_router(                                     # Register the analytics router as a sub-router of the v1 router
    analytics.router,                                          # The router object defined at the top of endpoints/analytics.py
    prefix="/analytics",                                       # All analytics routes are served under /api/v1/analytics/
    tags=["Analytics"],                                        # Groups all analytics endpoints under the 'Analytics' section in Swagger UI
)


# ---------------------------------------------------------------------------
# REGISTER AUTH ROUTES
# All routes defined in endpoints/auth.py are included here.
# prefix="/auth" means routes become /api/v1/auth/...
# ---------------------------------------------------------------------------

api_router.include_router(                                     # Register the auth router as a sub-router of the v1 router
    auth.router,                                               # The router object defined at the top of endpoints/auth.py
    prefix="/auth",                                            # All auth routes are served under /api/v1/auth/
    tags=["Auth"],                                             # Groups all auth endpoints under the 'Auth' section in Swagger UI
)


# ---------------------------------------------------------------------------
# PLACEHOLDER: future routers to be registered as the project grows
# Uncomment each block as you build the corresponding endpoint file.
# ---------------------------------------------------------------------------

# from app.api.v1.endpoints import auth
# api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])

# from app.api.v1.endpoints import sentiment
# api_router.include_router(sentiment.router, prefix="/sentiment", tags=["Sentiment"])

# from app.api.v1.endpoints import anomalies
# api_router.include_router(anomalies.router, prefix="/anomalies", tags=["Anomalies"])

# from app.api.v1.endpoints import forecast
# api_router.include_router(forecast.router, prefix="/forecast", tags=["Forecast"])

# from app.api.v1.endpoints import clusters
# api_router.include_router(clusters.router, prefix="/clusters", tags=["Clusters"])

# from app.api.v1.endpoints import portfolio
# api_router.include_router(portfolio.router, prefix="/portfolio", tags=["Portfolio"])

# from app.api.v1.endpoints import watchlist
# api_router.include_router(watchlist.router, prefix="/watchlist", tags=["Watchlist"])
