"""
schemas/auth.py — Pydantic request and response models for all auth API endpoints.

These classes define:
    - What JSON body the client must send (request models)
    - What JSON body FastAPI sends back (response models)

Routes that use these schemas:
    POST /auth/register  — RegisterRequest in, TokenOut out
    POST /auth/login     — LoginRequest in, TokenOut out
    GET  /auth/me        — no body in, UserOut out
"""

from pydantic import BaseModel, EmailStr    # BaseModel is the base class; EmailStr validates that a string is a valid email format
from typing import Optional                 # Optional marks fields that can be None
from datetime import datetime               # datetime used for the created_at timestamp on the user profile


# =============================================================================
# REQUEST SCHEMAS
# These define the shape of the JSON body the CLIENT sends to the API.
# =============================================================================

class RegisterRequest(BaseModel):
    """Body the client sends to POST /auth/register to create a new account."""

    email: str                              # User's email address — must be unique in Supabase auth.users
    password: str                           # Plain-text password — Supabase hashes it; we never store or log it
    full_name: Optional[str] = None         # Optional display name — stored in Supabase user metadata


class LoginRequest(BaseModel):
    """Body the client sends to POST /auth/login to get a JWT."""

    email: str                              # Email address of the account to log in with
    password: str                           # Plain-text password — sent to Supabase for verification


# =============================================================================
# RESPONSE SCHEMAS
# These define the shape of the JSON body the API sends back to the CLIENT.
# =============================================================================

class TokenOut(BaseModel):
    """
    Returned after a successful register or login.
    The client should store access_token and send it as:
        Authorization: Bearer <access_token>
    on all subsequent requests to protected endpoints.
    """

    access_token: str                       # The JWT the client must include in the Authorization header
    token_type: str = "bearer"              # Always "bearer" — tells the client how to format the Authorization header
    expires_in: int                         # Number of seconds until the token expires (e.g. 3600 = 1 hour)
    user_id: str                            # The authenticated user's UUID — matches auth.users.id in Supabase


class UserOut(BaseModel):
    """
    Returned by GET /auth/me — the current user's profile.
    Sourced from the Supabase auth user object, not from our own DB.
    """

    id: str                                 # The user's UUID from Supabase auth.users.id
    email: str                              # The user's email address
    full_name: Optional[str] = None         # Display name set during registration — may be None if not provided
    created_at: Optional[datetime] = None   # Timestamp when the account was created in Supabase


class AuthErrorOut(BaseModel):
    """Returned when auth fails — wraps Supabase's error message in a consistent format."""

    detail: str                             # Human-readable error message (e.g. "Invalid login credentials")
