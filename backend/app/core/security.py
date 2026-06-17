"""
security.py — JWT token validation and FastAPI auth dependency.

This file does two things:
    1. Validates incoming JWTs (issued by Supabase after login) using python-jose
    2. Provides get_current_user() — a FastAPI dependency injected into any route
       that requires the caller to be authenticated

How the auth flow works:
    Client calls POST /auth/login → Supabase issues a JWT
    Client stores the JWT and sends it as:  Authorization: Bearer <token>
    FastAPI routes that use Depends(get_current_user) call this file to validate it
    If valid, get_current_user() returns the user's UUID (from the JWT "sub" claim)
    That UUID is then used to filter watchlist/portfolio rows by user

IMPORTANT: For token validation to work, JWT_SECRET_KEY in your .env must be set
to the Supabase project's JWT secret, found at:
    Supabase Dashboard → Project Settings → API → JWT Settings → JWT Secret
"""

from fastapi import Depends, HTTPException, status   # Depends for injection; HTTPException for auth errors; status for HTTP codes
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials  # HTTPBearer extracts the Bearer token from the Authorization header
from jose import jwt, JWTError                       # python-jose: jwt.decode() validates and decodes a JWT; JWTError catches all JWT failures
from app.core.config import settings                 # Import shared settings to access JWT_SECRET_KEY and JWT_ALGORITHM from .env


# ---------------------------------------------------------------------------
# BEARER TOKEN EXTRACTOR
# HTTPBearer is a FastAPI security scheme.
# When injected via Depends(), it reads the Authorization header and extracts
# the token string from "Bearer <token>". Returns 403 if the header is missing.
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer()    # Create a reusable bearer token extractor — used as a dependency in get_current_user


# ---------------------------------------------------------------------------
# TOKEN VALIDATION UTILITY
# Decodes and validates a raw JWT string.
# Returns the full payload dict if valid; raises HTTPException if not.
# ---------------------------------------------------------------------------

def decode_token(token: str) -> dict:                # Accepts a raw JWT string and returns its decoded payload
    """
    Decodes and validates a Supabase JWT.

    Raises HTTP 401 if the token is:
        - Expired
        - Tampered with (signature mismatch)
        - Missing required claims
        - Malformed
    """
    try:
        payload = jwt.decode(                        # Decode the token — verifies signature and expiry automatically
            token,                                   # The raw JWT string from the Authorization header
            settings.JWT_SECRET_KEY,                 # The secret used to verify the signature (must match Supabase's JWT secret)
            algorithms=[settings.JWT_ALGORITHM],     # The signing algorithm — HS256 by default in Supabase
            options={"verify_aud": False},            # Skip audience verification — Supabase sets aud="authenticated" which we don't need to check
        )
        return payload                               # Return the decoded claims dict, e.g. {"sub": "user-uuid", "email": "...", "exp": ...}

    except JWTError as e:                            # Catch any JWT failure — expired, tampered, malformed
        raise HTTPException(                         # Convert to HTTP 401 Unauthorized so FastAPI returns the correct error
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {str(e)}",  # Include the specific reason for debugging
            headers={"WWW-Authenticate": "Bearer"},  # Standard header telling the client they need a Bearer token
        )


# ---------------------------------------------------------------------------
# CURRENT USER DEPENDENCY
# This is the main function injected into protected routes via Depends().
# It extracts the Bearer token, validates it, and returns the user's UUID.
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),  # HTTPBearer extracts the token from the Authorization header
) -> str:                                            # Returns a string: the user's UUID from the JWT "sub" claim
    """
    FastAPI dependency for protected routes.

    Usage in a route:
        @router.get("/portfolio")
        async def get_portfolio(user_id: str = Depends(get_current_user)):
            # user_id is now the authenticated user's UUID
            ...

    Raises HTTP 401 if the token is missing, expired, or invalid.
    Raises HTTP 403 if the Authorization header itself is missing (handled by HTTPBearer).
    """
    token = credentials.credentials                  # Extract the raw token string from "Bearer <token>"
    payload = decode_token(token)                    # Validate the token and get the claims dict

    user_id: str = payload.get("sub")               # Supabase puts the user's UUID in the "sub" (subject) claim
    if not user_id:                                  # If the "sub" claim is missing the token is unusable
        raise HTTPException(                         # Return 401 — token is technically valid but has no user identity
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing the user identity claim (sub).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id                                   # Return the user UUID — routes use this to scope their DB queries
