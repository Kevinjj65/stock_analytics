"""
auth.py — Authentication API endpoints.

All routes are served under /api/v1/auth/ (prefix set in router.py).

Routes defined here:
    POST /auth/register   — Create a new user account via Supabase auth
    POST /auth/login      — Log in with email/password, receive a JWT
    GET  /auth/me         — Return the currently logged-in user's profile (protected)

How Supabase auth works here:
    - We do NOT store passwords ourselves. Supabase manages password hashing and user storage.
    - We call Supabase's auth REST API via httpx (already in requirements.txt).
    - Supabase returns a JWT after successful register/login.
    - We pass that JWT straight to the client — they use it for all future authenticated requests.
    - The GET /auth/me route validates the JWT and fetches the user's profile from Supabase.

Supabase auth API base URL: {SUPABASE_URL}/auth/v1/
All calls require the header:  apikey: {SUPABASE_KEY}
"""

from fastapi import APIRouter, Depends, HTTPException, status   # APIRouter creates the router; Depends injects dependencies; status for HTTP codes
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials  # HTTPBearer extracts Bearer token; HTTPAuthorizationCredentials holds it
import httpx                                                     # Async HTTP client used to call Supabase's auth REST API

from app.core.config import settings                             # Access SUPABASE_URL, SUPABASE_KEY, and JWT settings from .env
from app.core.security import decode_token                       # JWT decode utility — validates token and returns the claims payload
from app.schemas.auth import (                                   # Import all auth-related Pydantic schemas
    RegisterRequest,                                             # Body shape for POST /auth/register
    LoginRequest,                                                # Body shape for POST /auth/login
    TokenOut,                                                    # Response shape after successful register or login
    UserOut,                                                     # Response shape for GET /auth/me
)

router = APIRouter()                                             # Create the auth router — registered in router.py with prefix="/auth"

bearer_scheme = HTTPBearer()                                     # Extracts "Bearer <token>" from the Authorization header on protected routes

SUPABASE_AUTH_URL = f"{settings.SUPABASE_URL}/auth/v1"          # Base URL for all Supabase auth API calls — built once at import time
SUPABASE_HEADERS = {                                             # Headers required on every Supabase auth API call
    "apikey": settings.SUPABASE_KEY,                             # The project's anon/public key — authorises the request to Supabase
    "Content-Type": "application/json",                          # Tell Supabase we're sending a JSON body
}


# =============================================================================
# REGISTER
# POST /api/v1/auth/register
# Creates a new user account in Supabase auth and returns a JWT
# =============================================================================

@router.post(
    "/register",                                                 # URL path: POST /api/v1/auth/register
    response_model=TokenOut,                                     # FastAPI validates and serialises the return value using TokenOut
    status_code=status.HTTP_201_CREATED,                         # HTTP 201 Created — correct status code for creating a new resource
    summary="Register a new user account",                       # Short label shown in Swagger UI at /docs
)
async def register(body: RegisterRequest):                       # body is the parsed and validated RegisterRequest JSON
    """
    Creates a new user account in Supabase auth.users.
    Returns a JWT the client should store for subsequent authenticated requests.
    Supabase handles password hashing — we never store or log the plain-text password.
    """
    payload = {                                                  # Build the JSON body for the Supabase signup endpoint
        "email": body.email,                                     # User's email address — Supabase enforces uniqueness
        "password": body.password,                               # Plain-text password — Supabase hashes it with bcrypt internally
    }

    if body.full_name:                                           # Only include metadata if the client provided a display name
        payload["data"] = {"full_name": body.full_name}          # Supabase stores this in auth.users.raw_user_meta_data as a JSON field

    async with httpx.AsyncClient() as client:                    # Open an async HTTP client — closed automatically after the block
        response = await client.post(                            # POST to Supabase's signup endpoint
            f"{SUPABASE_AUTH_URL}/signup",                       # Supabase signup URL: {SUPABASE_URL}/auth/v1/signup
            json=payload,                                        # Send the email, password, and optional name metadata
            headers=SUPABASE_HEADERS,                            # Include apikey and Content-Type headers
        )

    if response.status_code != 200:                              # Supabase returns 200 on success (not 201)
        error = response.json()                                  # Parse the error body from Supabase
        raise HTTPException(                                     # Forward Supabase's error message to the client
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error.get("msg") or error.get("message") or "Registration failed.",
        )

    data = response.json()                                       # Parse the success response body from Supabase

    if not data.get("access_token"):                             # Supabase may require email confirmation before issuing a token
        raise HTTPException(                                     # Tell the client to check their email
            status_code=status.HTTP_200_OK,
            detail="Registration successful. Please confirm your email before logging in.",
        )

    return TokenOut(                                             # Build and return the token response
        access_token=data["access_token"],                       # JWT issued by Supabase for this newly registered user
        token_type="bearer",                                     # Standard OAuth2 token type — always "bearer"
        expires_in=data.get("expires_in", 3600),                 # Seconds until the token expires — Supabase default is 3600 (1 hour)
        user_id=data["user"]["id"],                              # The new user's UUID from Supabase auth.users.id
    )


# =============================================================================
# LOGIN
# POST /api/v1/auth/login
# Authenticates with email/password and returns a JWT
# =============================================================================

@router.post(
    "/login",                                                    # URL path: POST /api/v1/auth/login
    response_model=TokenOut,                                     # Validated and serialised using TokenOut
    summary="Log in with email and password to receive a JWT",
)
async def login(body: LoginRequest):                             # body is the parsed LoginRequest — contains email and password
    """
    Authenticates the user with Supabase and returns a JWT.
    The returned access_token must be sent as 'Authorization: Bearer <token>'
    in the headers of all subsequent requests to protected endpoints.
    """
    async with httpx.AsyncClient() as client:                    # Open async HTTP client
        response = await client.post(                            # POST to Supabase's password login endpoint
            f"{SUPABASE_AUTH_URL}/token?grant_type=password",   # grant_type=password tells Supabase this is an email+password login
            json={                                               # Send credentials as a JSON body
                "email": body.email,                             # Email address of the account to authenticate
                "password": body.password,                       # Plain-text password — Supabase verifies it against the stored bcrypt hash
            },
            headers=SUPABASE_HEADERS,                            # Include apikey and Content-Type
        )

    if response.status_code != 200:                              # Any non-200 response means authentication failed
        error = response.json()                                  # Parse Supabase's error body
        raise HTTPException(                                     # Return 401 — do not hint whether the email or password was wrong
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error.get("msg") or error.get("error_description") or "Invalid email or password.",
        )

    data = response.json()                                       # Parse the successful login response

    return TokenOut(                                             # Build and return the token response
        access_token=data["access_token"],                       # JWT the client will use for all future authenticated requests
        token_type="bearer",                                     # Standard OAuth2 token type
        expires_in=data.get("expires_in", 3600),                 # Seconds until the token expires
        user_id=data["user"]["id"],                              # The authenticated user's UUID from Supabase
    )


# =============================================================================
# ME
# GET /api/v1/auth/me
# Returns the current user's profile — requires a valid JWT in Authorization header
# =============================================================================

@router.get(
    "/me",                                                       # URL path: GET /api/v1/auth/me
    response_model=UserOut,                                      # Validated and serialised using UserOut
    summary="Get the currently authenticated user's profile",
)
async def me(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),  # HTTPBearer reads "Bearer <token>" from the Authorization header
):
    """
    Returns the profile of the currently authenticated user.
    Requires a valid JWT in the Authorization header: Authorization: Bearer <token>
    Validates the token locally first, then fetches the full profile from Supabase.
    """
    token = credentials.credentials                              # Extract the raw JWT string from "Bearer <token>"
    payload = decode_token(token)                                # Validate the token locally — raises 401 if expired or tampered

    user_id = payload.get("sub")                                 # Extract the user UUID from the "sub" claim in the JWT
    email_from_token = payload.get("email", "")                  # Extract email from the JWT claims — Supabase includes it

    async with httpx.AsyncClient() as client:                    # Open async HTTP client to fetch full profile from Supabase
        response = await client.get(                             # GET the user's Supabase profile
            f"{SUPABASE_AUTH_URL}/user",                         # Supabase endpoint that returns the authenticated user's full profile
            headers={                                            # Headers for the Supabase /user endpoint
                **SUPABASE_HEADERS,                              # Include apikey and Content-Type
                "Authorization": f"Bearer {token}",             # Pass the raw JWT — Supabase uses this to identify the user
            },
        )

    if response.status_code == 200:                              # Supabase returned the full user object
        user_data = response.json()                              # Parse the user profile JSON from Supabase
        meta = user_data.get("user_metadata") or {}             # user_metadata holds fields like full_name stored at registration
        return UserOut(                                          # Build the user profile response from Supabase's data
            id=user_data.get("id", user_id),                    # User UUID — prefer Supabase response over token claim
            email=user_data.get("email", email_from_token),     # Email address from Supabase
            full_name=meta.get("full_name"),                     # Display name from registration metadata — None if not set
            created_at=user_data.get("created_at"),              # Account creation timestamp from Supabase
        )

    # Fallback: Supabase /user call failed — return what we can read from the JWT itself
    return UserOut(                                              # Return a minimal profile using only what's in the token
        id=user_id,                                              # UUID from the "sub" claim
        email=email_from_token,                                  # Email from the JWT "email" claim
    )
